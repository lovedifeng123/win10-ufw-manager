"""
UWF Manager Pro - 缓存清理核心（GUI 无关，可独立测试）

设计原则（先想清楚原理，再写代码）：
  * UWF 内存模式覆盖层容量很小（如 4GB），系统运行产生的临时文件、
    浏览器缓存、Windows 更新缓存等会迅速写满覆盖层 → 满则重启丢失、
    且频繁重启。
  * 清理的目的：在覆盖层快满时，删除这些无意义缓存，并用
    `uwfmgr file commit` 把「删除」固化到物理盘，真正释放覆盖层空间；
    更进一步，把这些临时目录加入 UWF 排除列表，使其根本不进覆盖层，
    清理结果永久生效（重启后也不被还原）。
  * 扫描与清理都是重 I/O，必须由调用方放在后台线程执行，并通过
    progress_cb 把进度回传 UI 线程绘制进度条，绝不能在 UI 线程做这些事。

模块不依赖 tkinter，便于单元测试（见 test_cache_cleaner.py）。
"""

import os
import glob
import shutil

# ==================== 清理规则定义（参考 Dism++ Data.xml）====================
# 每项: (标签, 路径模式列表, 说明, 是否默认勾选)
# 标签首词为分类 emoji，便于 UI 分组。
TARGETS = [
    # ===== 核心安全项：UWF 覆盖层下的临时缓存（删除后可自动重建）=====
    # 设计原则：只清理"纯缓存"，绝不碰用户资料、配置、日志、安装源。
    # 每一项都必须满足：(1)删除后软件正常运行 (2)不含用户个人数据

    ("📁 用户临时文件 %TEMP%", [
        os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "*"),
    ], "当前用户的临时文件目录（UWF 下主要占用来源）", True),

    ("📁 系统临时文件 Windows\\Temp", [
        r"C:\Windows\Temp\*",
    ], "系统级临时文件目录", True),

    # ===== Windows 更新缓存（UWF 下这些下载浪费覆盖层且重启即丢）=====

    ("🔄 Windows 更新下载缓存", [
        r"C:\Windows\SoftwareDistribution\Download\*",
    ], "已下载的补丁包（数百MB~数GB，UWF下重启也丢）", True),

    ("🔄 传递优化缓存", [
        r"C:\Windows\SoftwareDistribution\DeliveryOptimization\*",
    ], "P2P 传递优化服务缓存", True),

    # ===== 浏览器缓存（仅 Cache 子目录，不动配置/书签/密码/cookie）=====

    ("🌐 Chrome 浏览器缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\User Data\Default\Cache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\User Data\Default\Code Cache\*"),
    ], "仅缓存文件（不影响书签/密码/历史/扩展）", True),

    ("🌐 Edge 浏览器缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\User Data\Default\Cache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\User Data\Default\Code Cache\*"),
    ], "仅缓存文件（不影响书签/密码/历史/扩展）", True),

    # ===== 系统自动重建缓存 =====

    ("⚡ 预读取缓存 Prefetch", [
        r"C:\Windows\Prefetch\*",
    ], "系统预读取文件（删除后自动重建，首次启动稍慢）", True),

    ("⚡ 缩略图缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\Explorer\thumbcache_*.db"),
    ], "文件缩略图（删除后自动重建，首次打开文件夹稍慢）", False),
]


def _human(n):
    try:
        n = int(n)
    except Exception:
        n = 0
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _name(label):
    """去掉首词 emoji，返回可读名称。"""
    return label.split(" ", 1)[1] if " " in label else label


# ==================== 安全护栏（防止误删整个启动目录的致命 bug）====================
# 历史事故根因：清理第一项用 os.path.join(os.environ.get("TEMP", ""), "*")，
# 当 TEMP 为空时退化为裸通配符 "*"，glob.glob("*") 会匹配【启动程序所在的整个
# 文件夹】并把它全部删除。以下护栏确保：任何目标都必须解析为绝对且明确的缓存
# 目录，否则整项跳过；删除前再二次校验路径确实位于预期目录内。

def _abs_base(pattern):
    """把路径模式解析为其"基准目录"（绝对、安全）。不安全则返回 None。

    不安全的情况：
      * 展开/拼接后为空（如 TEMP 为空导致 join("", "*") == "*"）
      * 相对路径（依赖当前工作目录 CWD，极易误删）
      * 盘符根（如 C:\）或用户主目录本身
    """
    expanded = os.path.expandvars(pattern).strip()
    if not expanded:
        return None
    if not os.path.isabs(expanded):
        # 相对路径依赖 CWD，直接拒绝
        return None
    if expanded.endswith("*"):
        base = os.path.dirname(expanded.rstrip("\\/"))
    elif "*" in expanded:
        base = os.path.dirname(expanded)
    else:
        base = expanded
    base = os.path.abspath(base)
    if not _base_ok(base):
        return None
    return base


def _base_ok(base):
    if not base or not os.path.isabs(base):
        return False
    # 拒绝盘符根：C:\ 或 C:
    drive, rest = os.path.splitdrive(base)
    if drive and (rest in ("", "\\")):
        return False
    # 拒绝用户主目录本身（其下明确子目录允许，如 AppData、Documents）
    up = os.path.abspath(os.environ.get("USERPROFILE", ""))
    if up and os.path.abspath(base) == up:
        return False
    return True


def _is_root_or_home(path):
    """路径是否为盘符根（C:\）或用户主目录本身。"""
    p = os.path.abspath(path)
    drive, rest = os.path.splitdrive(p)
    if drive and (rest in ("", "\\")):
        return True
    up = os.path.abspath(os.environ.get("USERPROFILE", ""))
    if up and p == up:
        return True
    return False


def _safe_to_remove(fpath, base):
    """被删路径 fpath 必须严格位于预期基准目录 base 内。"""
    if not base:
        return False
    bp = os.path.abspath(base)
    if not os.path.isabs(bp):
        return False
    fp = os.path.abspath(fpath)
    if _is_root_or_home(fp):
        return False  # 防御：绝不删盘符根/用户主目录本身
    if fp == bp:
        return True
    return fp.startswith(bp + os.sep)


# ==================== 扫描 ====================

def _scan_one(patterns):
    """计算一组路径模式的总大小与文件数。"""
    size = 0
    count = 0
    for pat in patterns:
        if _abs_base(pat) is None:
            continue  # 不安全（如环境变量为空）→ 跳过该项，绝不误删
        expanded = os.path.expandvars(pat)
        for fpath in glob.glob(expanded):
            try:
                if os.path.isfile(fpath):
                    try:
                        size += os.path.getsize(fpath)
                        count += 1
                    except OSError:
                        pass
                elif os.path.isdir(fpath):
                    for root, _dirs, files in os.walk(fpath):
                        for fn in files:
                            try:
                                size += os.path.getsize(os.path.join(root, fn))
                                count += 1
                            except OSError:
                                continue
            except (OSError, PermissionError):
                continue
    return size, count


def scan_targets(targets, progress_cb=None, cancel_event=None):
    """扫描所有清理目标，返回每项大小与文件数。

    progress_cb(percent:int, message:str) 在后台线程被调用，调用方负责
    把它安全地转发到 UI 线程。cancel_event 被设置时立即停止扫描。

    返回: {"results": [{label, desc, patterns, default, size, count}],
           "cancelled": bool}
    """
    results = []
    total = len(targets)
    for i, (label, patterns, desc, default) in enumerate(targets):
        if cancel_event is not None and cancel_event.is_set():
            break
        size, count = _scan_one(patterns)
        results.append({
            "label": label, "desc": desc, "patterns": patterns,
            "default": default, "size": size, "count": count,
        })
        if progress_cb is not None:
            pct = min(100, int(100 * (i + 1) / total))
            progress_cb(pct, f"扫描: {_name(label)}")
    if progress_cb is not None:
        progress_cb(100, "扫描完成")
    cancelled = bool(cancel_event is not None and cancel_event.is_set())
    return {"results": results, "cancelled": cancelled}


# ==================== 清理 ====================

def _collect_commit_targets(patterns):
    """根据路径模式收集 UWF 提交目标：目录通配→提交该目录；单文件→提交删除。"""
    commit_dirs = set()
    commit_files = []
    for pat in patterns:
        if _abs_base(pat) is None:
            continue  # 不安全目标不参与提交，避免误提交根目录
        expanded = os.path.expandvars(pat)
        if expanded.endswith("*"):
            commit_dirs.add(os.path.dirname(expanded.rstrip("\\/")).rstrip("\\/"))
        elif "*" in expanded:
            commit_dirs.add(os.path.dirname(expanded).rstrip("\\/"))
        else:
            commit_files.append(expanded)
    return commit_dirs, commit_files


def clean_targets(selected, progress_cb=None, cancel_event=None,
                  do_commit=False, do_exclude=False):
    """清理所选目标，回传字节级百分比进度。

    selected: scan_targets 返回的 results 子列表（每项含 label/patterns/size）
    progress_cb(percent:int, message:str)
    cancel_event: 设置后尽快停止（完成已删除部分的提交）
    do_commit/do_exclude: 是否在 UWF 启用时提交删除 / 加入排除

    返回 summary 字典（含 total_freed / details / cancelled 等）。
    """
    total_bytes = sum(max(0, s.get("size", 0)) for s in selected)
    done_bytes = 0
    details = []
    all_commit_dirs = set()
    all_commit_files = []
    cancelled = False
    last_pct = -1

    # UWF 是否启用（决定是否真正提交/排除）
    uwf = None
    uwf_on = False
    if (do_commit or do_exclude):
        try:
            import uwf_core  # 懒加载，纯文件测试无需 pywin32
            uwf = uwf_core.UWFCore()
            uwf.connect()
            uwf_on = bool((uwf.get_filter() or {}).get("CurrentEnabled"))
        except Exception:
            uwf_on = False
    if not uwf_on:
        do_commit = False
        do_exclude = False

    for s in selected:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        label = s["label"]
        patterns = s["patterns"]
        commit_dirs, commit_files = _collect_commit_targets(patterns)
        all_commit_dirs |= commit_dirs
        all_commit_files += commit_files

        freed = 0
        count = 0
        for pat in patterns:
            base = _abs_base(pat)
            if base is None:
                continue  # 不安全（如环境变量为空）→ 跳过，绝不误删
            expanded = os.path.expandvars(pat)
            try:
                for fpath in glob.glob(expanded):
                    if not _safe_to_remove(fpath, base):
                        continue  # 二次校验：只删预期目录内的内容
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
                    try:
                        if os.path.isfile(fpath):
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            freed += sz
                            done_bytes += sz
                            count += 1
                        elif os.path.isdir(fpath):
                            shutil.rmtree(fpath, ignore_errors=True)
                            count += 1
                    except (OSError, PermissionError, FileNotFoundError):
                        continue
                    # 基于已释放字节更新进度（平滑、必达 100%）
                    if total_bytes > 0:
                        pct = int(100 * done_bytes / total_bytes)
                        if pct != last_pct:
                            last_pct = pct
                            if progress_cb is not None:
                                progress_cb(min(pct, 99),
                                            f"清理: {_name(label)}  "
                                            f"({_human(done_bytes)}/{_human(total_bytes)})")
                if cancelled:
                    break
            except (OSError, PermissionError):
                continue
        if progress_cb is not None:
            shown = min(99, last_pct if last_pct > 0 else 1)
            progress_cb(shown, f"清理: {_name(label)} 完成")
        if freed > 0 or count > 0:
            details.append(f"  {label}: {_human(freed)} ({count} 项)")

    # ==================== UWF 提交：让删除真正释放覆盖层 ====================
    commit_done = 0
    if do_commit and uwf_on and uwf is not None:
        if progress_cb is not None:
            progress_cb(96, "正在提交删除到物理盘 (UWF)…")
        try:
            uwf.batch_commit(list(all_commit_dirs), list(all_commit_files))
            commit_done = len(all_commit_dirs) + len(all_commit_files)
        except Exception as e:  # noqa
            details.append(f"  ⚠️ UWF 提交失败: {e}")
    if commit_done:
        details.append(
            f"  ✅ UWF 提交：已固化 {commit_done} 项删除到物理盘"
            f"（重启后保留，真正释放覆盖层）")

    # ==================== UWF 排除：永久生效（重启后）====================
    exclude_done = 0
    if do_exclude and uwf_on and uwf is not None:
        if progress_cb is not None:
            progress_cb(98, "正在将目录加入 UWF 排除列表…")
        try:
            excl = list(all_commit_dirs) + list(all_commit_files)
            uwf.batch_exclude("c", excl)
            exclude_done = len(excl)
        except Exception as e:  # noqa
            details.append(f"  ⚠️ UWF 排除失败: {e}")
    if exclude_done:
        details.append(
            f"  ✅ UWF 排除：已加入 {exclude_done} 个目录/文件"
            f"（重启后永久生效，未来不再占用覆盖层）")

    if progress_cb is not None:
        progress_cb(100, "清理完成")
    return {
        "total_freed": done_bytes,
        "details": details,
        "cancelled": cancelled,
        "commit_done": commit_done,
        "exclude_done": exclude_done,
    }
