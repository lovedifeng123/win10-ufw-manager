"""
UWF Manager Pro - 核心模块（CLI + WMI 混合实现）

设计要点：
  * 所有「写入」操作（启用/禁用/保护/排除/阈值/重启/关机/HORM 等）
    统一走官方命令行 uwfmgr.exe，规避 win32com 直接调用 WMI 方法时
    把方法误解析成 int 属性导致 'int' object is not callable 的坑。
  * 所有「读取」操作仍走 WMI（root\\standardcimv2\\embedded），稳定可靠。

支持的 WMI 类：
  UWF_Filter / UWF_Volume / UWF_Overlay / UWF_OverlayConfig
"""
import os
import sys
import time
import tempfile
import subprocess
import ctypes
import win32api
import win32con
import win32event
import win32com.client


class UWFError(Exception):
    """UWF 操作失败（写操作或读取异常）。"""
    pass


class UWFNotSupported(Exception):
    """当前系统未启用 / 不支持 UWF。"""
    pass


UWFMGR = None  # 延迟解析，绕过 32 位进程 System32 重定向
UWF_CORE_VERSION = "2.7"


def _resolve_uwfmgr():
    candidates = [
        r"C:\Windows\System32\uwfmgr.exe",
        r"C:\Windows\Sysnative\uwfmgr.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


def _is_admin():
    """当前进程是否以管理员权限运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_direct(args):
    """已提权（或无需提权）时直接调用 uwfmgr.exe。"""
    global UWFMGR
    if UWFMGR is None:
        UWFMGR = _resolve_uwfmgr()
    try:
        proc = subprocess.run([UWFMGR] + [str(a) for a in args],
                              capture_output=True, timeout=120,
                              shell=False)
    except FileNotFoundError:
        raise UWFError("找不到 uwfmgr.exe，请确认系统已启用 UWF 功能。")
    except subprocess.TimeoutExpired:
        raise UWFError("uwfmgr.exe 执行超时。")
    except Exception as e:
        raise UWFError(f"调用 uwfmgr.exe 失败: {e}")
    out = proc.stdout.decode("gbk", errors="ignore") if proc.stdout else ""
    err = proc.stderr.decode("gbk", errors="ignore") if proc.stderr else ""
    return proc.returncode, out, err


def _run_elevated(args):
    """非管理员时，通过 UAC(runas) 提权执行 uwfmgr.exe。

    uwfmgr.exe 自带 requireAdministrator 清单，runas 会弹出 UAC 请求；
    用户同意后以管理员身份执行写操作。返回空输出，由调用方重新读取
    WMI 状态来确认结果。
    """
    global UWFMGR
    if UWFMGR is None:
        UWFMGR = _resolve_uwfmgr()
    params = " ".join(
        f'"{a}"' if (" " in str(a) or "\t" in str(a)) else str(a)
        for a in args)
    try:
        info = win32api.ShellExecuteEx(
            fMask=win32con.SEE_MASK_NOCLOSEPROCESS,
            hwnd=0,
            lpVerb="runas",
            lpFile=UWFMGR,
            lpParameters=params,
            nShow=1,
        )
    except Exception as e:
        raise UWFError(f"无法请求管理员权限：{e}")
    hproc = info.get("hProcess")
    if not hproc:
        # 用户拒绝了 UAC 授权
        raise UWFError("已取消管理员授权（UAC 被拒绝），操作未执行。")
    try:
        win32event.WaitForSingleObject(hproc, 20000)
    except Exception:
        pass
    return 0, "", ""


def _cli(args):
    """调用 uwfmgr.exe，成功返回 stdout 文本，失败抛 UWFError。

    若当前非管理员，自动通过 UAC 提权执行（仅写操作需要，弹一次 UAC）。
    """
    if _is_admin():
        rc, out, err = _run_direct(args)
    else:
        rc, out, err = _run_elevated(args)
    if rc != 0:
        msg = (err or out).strip() or f"uwfmgr 返回码 {rc}"
        raise UWFError(f"操作失败 [{ ' '.join(map(str, args)) }]: {msg}")
    combined = (out + err)
    if "失败" in combined or "拒绝访问" in combined or "拒绝" in combined:
        raise UWFError(
            f"操作失败 [{ ' '.join(map(str, args)) }]: {combined.strip()}")
    return out


def _norm_drive(drive_letter):
    """规范化盘符为 'c:' 形式（uwfmgr 接受大小写）。"""
    d = (drive_letter or "C:").strip()
    if len(d) >= 2 and d[1] == ":":
        return d[0].lower() + ":"
    return d.lower()


def _full_path(drive_letter, rel_path):
    """把 (盘符, 相对卷根路径) 拼成 uwfmgr 需要的完整路径。
    rel_path 形如 '\\Program Files\\Huorong' 或 'Program Files\\Huorong'。
    """
    d = _norm_drive(drive_letter)
    p = (rel_path or "").replace("/", "\\").strip()
    if len(p) >= 2 and p[1] == ":":
        return p  # 已经是完整路径
    if not p.startswith("\\"):
        p = "\\" + p
    return d + p


def _variant_to_py(val):
    return val


# ==================== 核心类 ====================

class UWFCore:
    """UWF 功能核心封装。所有方法在异常时抛出 UWFError。"""

    def __init__(self):
        self._wmi = None
        self._connected = False

    # ---------- 连接管理 ----------
    def connect(self):
        try:
            self._wmi = win32com.client.GetObject(
                r"winmgmts:\\.\root\standardcimv2\embedded")
            self._connected = True
        except Exception as e:
            self._connected = False
            raise UWFNotSupported(f"无法连接到 UWF WMI: {e}") from e
        return True

    @property
    def connected(self):
        return self._connected

    def _require_conn(self):
        if not self._connected:
            self.connect()

    def _first(self, class_name):
        self._require_conn()
        try:
            for it in self._wmi.InstancesOf(class_name):
                return it
        except Exception:
            return None
        return None

    def _all(self, class_name):
        self._require_conn()
        result = []
        try:
            for it in self._wmi.InstancesOf(class_name):
                result.append(it)
        except Exception:
            pass
        return result

    # ==================== 状态查询（WMI）====================

    def get_filter(self):
        f = self._first("UWF_Filter")
        if f is None:
            return {"CurrentEnabled": False, "NextEnabled": False,
                    "ShutdownPending": False, "HORMEnabled": False}
        out = {}
        for prop in ("CurrentEnabled", "NextEnabled", "ShutdownPending",
                     "HORMEnabled"):
            try:
                out[prop] = _variant_to_py(getattr(f, prop))
            except Exception:
                out[prop] = None
        return out

    def get_volumes(self):
        """返回按盘符聚合后的卷状态。

        UWF_Volume 每个盘符会有两条实例：一条 CurrentSession=True
        （当前会话实际状态），一条 CurrentSession=False（下次重启后状态）。
        必须按盘符聚合，否则会误读。

        返回字段：
          DriveLetter       盘符
          CurrentProtected 当前会话是否已保护（最权威）
          NextProtected    下次重启后是否保护（None=与当前一致/未知）
          CommitPending    是否有提交待处理
        """
        vols = self._all("UWF_Volume")
        by_drive = {}
        for v in vols:
            dl = (getattr(v, "DriveLetter", None) or "?")
            cur = bool(getattr(v, "CurrentSession", False))
            prot = getattr(v, "Protected", None)
            entry = by_drive.setdefault(
                dl, {"DriveLetter": dl, "CurrentProtected": False,
                     "NextProtected": None, "CommitPending": False})
            if cur:
                entry["CurrentProtected"] = bool(prot)
            else:
                entry["NextProtected"] = bool(prot) if prot is not None else None
            if getattr(v, "CommitPending", False):
                entry["CommitPending"] = True
        return list(by_drive.values())

    def get_overlay_config(self):
        # 优先读取“下次会话”实例（与 UWFPRO 显示/编辑口径一致），
        # 退回当前会话实例，再退回空。
        cfgs = self._all("UWF_OverlayConfig")
        chosen = None
        for c in cfgs:
            try:
                if not bool(getattr(c, "CurrentSession", True)):
                    chosen = c
                    break
            except Exception:
                pass
        if chosen is None and cfgs:
            chosen = cfgs[0]
        if chosen is None:
            return {"Type": None, "MaximumSize": None}
        out = {}
        for prop in ("Type", "MaximumSize"):
            try:
                out[prop] = _variant_to_py(getattr(chosen, prop))
            except Exception:
                out[prop] = None
        return out

    def get_overlay(self):
        o = self._first("UWF_Overlay")
        if o is None:
            return None
        out = {}
        for prop in ("AvailableSpace", "OverlayConsumption",
                     "CriticalOverlayThreshold", "WarningOverlayThreshold"):
            try:
                out[prop] = _variant_to_py(getattr(o, prop))
            except Exception:
                out[prop] = None
        return out

    def get_exclusions(self, drive_letter=None):
        """返回排除列表。必须用 ExecMethod_('GetExclusions')，
        直接 v.GetExclusions() 在 win32com 下会被误解析。

        注意：UWF_Volume 存在「当前会话」与「下次会话」两个实例，
        二者返回的排除项相同，这里按 (盘符, 路径) 去重，避免界面
        与测试中重复计数。
        """
        vols = self._all("UWF_Volume")
        seen = set()
        results = []
        for v in vols:
            dl = getattr(v, "DriveLetter", None)
            if drive_letter and dl != drive_letter:
                continue
            try:
                result = v.ExecMethod_("GetExclusions")
                excl_list = getattr(result, "ExcludedFiles", None)
                if excl_list:
                    for item in excl_list:
                        fname = getattr(item, "FileName", None)
                        if not fname:
                            continue
                        key = (dl, str(fname))
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append({"drive": dl, "path": str(fname)})
            except Exception:
                pass
        return results

    # ==================== 写入过滤（CLI）====================

    def enable_filter(self):
        _cli(["filter", "enable"])
        return True

    def disable_filter(self):
        _cli(["filter", "disable"])
        return True

    # ==================== 覆盖配置（CLI）====================

    def set_overlay_type(self, overlay_type):
        """0=基于内存(RAM), 1=基于磁盘(DISK)。"""
        kind = "RAM" if int(overlay_type) == 0 else "DISK"
        _cli(["overlay", "set-type", kind])
        return True

    def set_maximum_size(self, size_mb):
        cfg = self.get_overlay_config()
        if cfg.get("Type") == 0 and int(size_mb) > 1024:
            raise UWFError(
                "RAM 模式覆盖层上限为 1024 MB；如需更大缓存，请先将"
                "「覆盖类型」切换为「基于磁盘」后再设置。")
        _cli(["overlay", "set-size", str(int(size_mb))])
        return True

    def set_warning_threshold(self, size_mb):
        _cli(["overlay", "set-warningthreshold", str(int(size_mb))])
        return True

    def set_critical_threshold(self, size_mb):
        _cli(["overlay", "set-criticalthreshold", str(int(size_mb))])
        return True

    # ==================== 分区保护（CLI）====================

    def protect_volume(self, drive_letter, current_session=True):
        _cli(["volume", "protect", _norm_drive(drive_letter)])
        return True

    def unprotect_volume(self, drive_letter, current_session=True):
        _cli(["volume", "unprotect", _norm_drive(drive_letter)])
        return True

    # ==================== 排除列表（CLI）====================

    def add_exclusion(self, drive_letter, file_path):
        _cli(["file", "add-exclusion", _full_path(drive_letter, file_path)])
        return True

    def remove_exclusion(self, drive_letter, file_path):
        _cli(["file", "remove-exclusion", _full_path(drive_letter, file_path)])
        return True

    def remove_all_exclusions(self, drive_letter):
        excl = self.get_exclusions(drive_letter)
        if not excl:
            return True
        last_err = None
        for e in excl:
            try:
                self.remove_exclusion(e["drive"], e["path"])
            except Exception as ex:
                last_err = ex
        if last_err and self.get_exclusions(drive_letter):
            raise last_err
        return True

    # ==================== 文件/注册表提交（CLI）====================

    def commit_file(self, drive_letter, file_path):
        _cli(["file", "commit", _full_path(drive_letter, file_path)])
        return True

    def commit_file_deletion(self, drive_letter, file_path):
        _cli(["file", "commit-delete", _full_path(drive_letter, file_path)])
        return True

    def commit_all_deletions(self):
        """批量提交删除：uwfmgr CLI 无对应命令，且 WMI 方法在本环境
        不可用，故退化为提示用户使用单文件提交或重启。"""
        raise UWFError(
            "当前环境暂不支持「批量提交删除」。"
            "请在「文件分析」或「写入日志」中对单个文件使用「提交删除」，"
            "或重启计算机以丢弃覆盖层。")

    # ==================== HORM（CLI）====================

    def enable_horm(self):
        _cli(["filter", "enable-HORM"])
        return True

    def disable_horm(self):
        _cli(["filter", "disable-HORM"])
        return True

    # ==================== 重启/关机（CLI）====================

    def restart_system(self):
        _cli(["restart"])
        return True

    def shutdown_system(self):
        _cli(["shutdown"])
        return True

    # ==================== 重置（CLI）====================

    def reset_settings(self):
        _cli(["filter", "reset-settings"])
        return True

    # ==================== 覆盖文件查询（WMI，本机可能挂死）====================

    def get_overlay_files(self, drive_letter):
        """返回覆盖层中（当前会话）的文件列表，每项 {path, size(字节)}。
        走 WMI UWF_Overlay.GetOverlayFiles（只读）。UWFPRO 源码注释称此
        方法“有问题”，本机实测可能挂死，故放在子线程执行并加超时，
        超时/失败一律返回空列表，绝不影响主线程与界面。"""
        import threading

        result = {"files": []}
        exc = {}

        def _worker():
            try:
                o = self._first("UWF_Overlay")
                if o is None:
                    return
                in_params = o.Methods_("GetOverlayFiles").InParameters
                in_params.Properties_("Volume").Value = drive_letter
                out = o.ExecMethod_("GetOverlayFiles", in_params)
                arr = getattr(out, "OverlayFiles", None)
                if arr:
                    for item in arr:
                        result["files"].append({
                            "path": str(getattr(item, "FileName", "") or ""),
                            "size": int(getattr(item, "FileSize", 0) or 0),
                        })
            except Exception as e:  # noqa
                exc["e"] = e

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=15)
        if t.is_alive():
            # 超时：放弃该只读查询，返回空列表（已知问题，不阻断）
            return []
        return result["files"]

    # ==================== 注册表排除（与 UWFPRO 对齐）====================

    def get_registry_exclusions(self):
        """返回注册表排除列表（当前+下次会话合并去重）。
        读取走 WMI UWF_RegistryFilter.GetExclusions（只读，无需提权）。"""
        results = []
        try:
            for inst in self._all("UWF_RegistryFilter"):
                try:
                    out = inst.ExecMethod_("GetExclusions")
                    arr = getattr(out, "ExcludedKeys", None)
                    if arr:
                        for item in arr:
                            key = getattr(item, "RegistryKey", None)
                            if key and str(key) not in results:
                                results.append(str(key))
                except Exception:
                    continue
        except Exception:
            pass
        return results

    def add_registry_exclusion(self, key):
        _cli(["registry", "add-exclusion", key])
        return True

    def remove_registry_exclusion(self, key):
        _cli(["registry", "remove-exclusion", key])
        return True

    def commit_registry(self, key, value):
        _cli(["registry", "commit", key, value])
        return True

    # ==================== 服务模式（UWFPRO 参考）====================

    def get_servicing(self):
        """返回服务模式状态 {CurrentEnabled, NextEnabled}。
        读取 UWF_Servicing WMI（若存在）。"""
        out = {"CurrentEnabled": None, "NextEnabled": None}
        try:
            for inst in self._all("UWF_Servicing"):
                cur = bool(getattr(inst, "CurrentSession", False))
                en = getattr(inst, "ServicingEnabled", None)
                if cur:
                    out["CurrentEnabled"] = bool(en) if en is not None else None
                else:
                    out["NextEnabled"] = bool(en) if en is not None else None
        except Exception:
            pass
        return out

    def _servicing_next_instance(self):
        """返回「下次会话」的 UWF_Servicing 实例（用于启用/禁用）。"""
        try:
            for inst in self._all("UWF_Servicing"):
                if getattr(inst, "CurrentSession", None) is False:
                    return inst
        except Exception:
            pass
        return None

    def set_servicing(self, enable):
        """启用/禁用服务模式（下次会话生效）。

        实现说明：本机实测 uwfmgr.exe servicing 子命令返回
        「当前系统不支持」（0x85E00005），但 WMI UWF_Servicing
        的 Enable/Disable 方法实际可用——只是 win32com 会误报
        0x80041001（WBEM_E_FAILED）的 HRESULT。因此这里直接调用
        WMI 方法，吞掉该误报错误，再以「读回的实际状态」做二次校验：
        状态符合预期即视为成功；否则抛出清晰的不支持提示。
        """
        target = bool(enable)
        inst = self._servicing_next_instance()
        if inst is None:
            # 极少数系统无 WMI 类，回退到官方 CLI（不支持时会给出明确错误）
            _cli(["servicing", "enable" if target else "disable"])
            return True
        try:
            inst.ExecMethod_("Enable" if target else "Disable")
        except Exception:
            # 吞掉 win32com 的误报 HRESULT（0x80041001），实际已生效
            pass
        svc = self.get_servicing() or {}
        if bool(svc.get("NextEnabled")) != target:
            raise UWFError(
                f"服务模式{'启用' if target else '禁用'}失败："
                "当前系统可能不支持 UWF Servicing（需特定 Windows 版本）。")
        return True
