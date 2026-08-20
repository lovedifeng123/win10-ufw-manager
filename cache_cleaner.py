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
    # ===== 1. 系统临时文件 =====
    ("📁 用户临时文件", [
        os.path.join(os.environ.get("TEMP", ""), "*"),
        os.path.join(os.environ.get("TEMP", ""), ".*"),
    ], "%TEMP% 用户临时目录", True),
    ("📁 系统临时文件", [r"C:\Windows\Temp\*"], r"C:\Windows\Temp", True),
    ("📁 驱动解压残留 (Intel/AMD/NVIDIA)", [
        r"C:\AMD\*", r"C:\Intel\*", r"C:\NVIDIA\*", r"C:\Prog\*",
    ], "显卡/芯片组驱动安装解压目录", True),
    ("📁 Windows 升级残留 ($Windows.*)", [
        r"C:\$Windows.~BT\*", r"C:\$Windows.~WS\*", r"C:\$Windows.~LS\*",
    ], "系统升级/还原后遗留的临时文件", False),

    # ===== 2. Windows 更新缓存 =====
    ("🔄 Windows 更新下载缓存", [
        r"C:\Windows\SoftwareDistribution\Download\*",
    ], "Windows Update 已下载的补丁包（数百MB~数GB）", True),
    ("🔄 传递优化缓存 (DeliveryOptimization)", [
        r"C:\Windows\SoftwareDistribution\DeliveryOptimization\*",
    ], "Win10 传递优化服务 P2P 缓存", True),
    ("🔄 Windows 更新记录 (DataStore)", [
        r"C:\Windows\SoftwareDistribution\DataStore\*",
    ], "Update 安装历史记录数据库", False),

    # ===== 3. 日志与报告 =====
    ("📋 Windows 错误报告 (WER)", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\WER\*"),
        os.path.join(os.environ.get("PROGRAMDATA", ""),
                     r"Microsoft\Windows\WER\*"),
    ], "程序崩溃/错误报告文件", True),
    ("📋 Windows 事件日志", [
        r"C:\Windows\System32\winevt\Logs\*.evtx",
    ], "Windows Event Log 日志文件", True),
    ("📋 系统崩溃转储 (.dmp)", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"CrashDumps\*.dmp"),
        r"C:\Windows\MEMORY.DMP", r"C:\Windows\Minidump\*",
    ], "蓝屏/程序崩溃内存转储（可能很大）", True),
    ("📋 Windows 日志文件 (*.log)", [
        r"C:\Windows\Panther\*.log", r"C:\Windows\Panther\*.xml",
        r"C:\Windows\Logs\CBS\*.log", r"C:\WinSxS\ManifestCache\*",
        r"C:\CbsTemp\*",
    ], "系统组件安装/更新日志", True),
    ("📋 回收站", [r"C:\$Recycle.Bin\*"], "所有用户回收站内容", False),

    # ===== 4. 浏览器与网络缓存 =====
    ("🌐 Chrome 缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\User Data\Default\Cache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\User Data\Default\Code Cache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\User Data\Default\Service Worker\CacheStorage\*"),
    ], "Chrome 浏览器缓存 + Code Cache", True),
    ("🌐 Edge 缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\User Data\Default\Cache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Edge\User Data\Default\Code Cache\*"),
    ], "Edge 浏览器缓存", True),
    ("🌐 IE/WinINet 网页缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\INetCache\*"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\INetCookies\*"),
    ], "IE/系统组件网页缓存和 Cookies", True),
    ("🌐 Terminal Server Client 缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Terminal Server Client\*"),
    ], "远程桌面客户端缓存", True),

    # ===== 5. 系统加速缓存 =====
    ("⚡ Windows 预读取 (Prefetch)", [r"C:\Windows\Prefetch\*"],
     "Prefetch 预读取文件（会自动重建）", True),
    ("⚡ 缩略图缓存 (Thumbcache)", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\Explorer\thumbcache_*.db"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Microsoft\Windows\Explorer\IconCache.db"),
    ], "缩略图缓存（需重启资源管理器）", True),
    ("⚡ .NET Native Images 缓存", [
        r"C:\Windows\assembly\NativeImages_v4.0_64\temp\*",
        r"C:\Windows\assembly\NativeImages_v4.0_64\tmp\*",
        r"C:\Windows\assembly\temp\*", r"C:\Windows\assembly\tmp\*",
    ], ".NET 程序集原生镜像缓存（会自动重建）", True),
    ("⚡ NuGet 包缓存", [
        os.path.join(os.environ.get("USERPROFILE", ""), r".nuget\packages\*"),
    ], ".NET 开发 NuGet 下载包缓存", False),

    # ===== 6. 应用程序缓存 =====
    ("📦 Office 安装源 (ClickToRun)", [
        os.path.join(os.environ.get("PROGRAMDATA", ""),
                     r"Microsoft\ClickToRun\Packages\*"),
    ], "Office 365/2016 ClickToRun 安装源", False),
    ("📦 Office 本地安装源 (MSOCache)", [r"C:\MSOCache\*"],
     "Office 传统安装源文件", False),
    ("📦 Windows Installer 补丁缓存", [
        r"C:\Windows\Installer\$PatchCache$\*",
    ], "MSP 补丁安装缓存（实验性）", False),
    ("📦 PDB 符号调试缓存", [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"DBG\*"),
    ], "Visual Studio 调试符号缓存", False),
    ("📦 腾讯软件临时文件 (QQ等)", [
        os.path.join(os.environ.get("APPDATA", ""), r"Tencent\AndroidAssist\*"),
        os.path.join(os.environ.get("APPDATA", ""), r"Tencent\Logs\*"),
        os.path.join(os.environ.get("APPDATA", ""), r"Tencent\WinTemp\*"),
    ], "QQ/腾讯软件临时文件和日志", True),
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


# ==================== 扫描 ====================

def _scan_one(patterns):
    """计算一组路径模式的总大小与文件数。"""
    size = 0
    count = 0
    for pat in patterns:
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
            expanded = os.path.expandvars(pat)
            try:
                for fpath in glob.glob(expanded):
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
