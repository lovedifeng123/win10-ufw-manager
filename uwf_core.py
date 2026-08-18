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
import subprocess
import win32com.client

UWFMGR = r"C:\Windows\System32\uwfmgr.exe"


class UWFError(Exception):
    """UWF 相关异常基类"""
    pass


class UWFNotSupported(UWFError):
    """当前系统不支持 UWF（通常因为没有 embedded 命名空间）"""
    pass


# ==================== uwfmgr.exe 封装 ====================

def _run_cli(args):
    """调用 uwfmgr.exe，返回 (returncode, stdout_text, stderr_text)。"""
    try:
        proc = subprocess.run([UWFMGR] + list(args),
                               capture_output=True, timeout=90)
    except FileNotFoundError:
        raise UWFError("找不到 uwfmgr.exe，请确认系统已启用 UWF 功能。")
    except subprocess.TimeoutExpired:
        raise UWFError("uwfmgr.exe 执行超时。")
    except Exception as e:
        raise UWFError(f"调用 uwfmgr.exe 失败: {e}")
    out = proc.stdout.decode("gbk", errors="ignore") if proc.stdout else ""
    err = proc.stderr.decode("gbk", errors="ignore") if proc.stderr else ""
    return proc.returncode, out, err


def _cli(args):
    """调用 uwfmgr.exe，成功返回 stdout 文本，失败抛 UWFError。"""
    rc, out, err = _run_cli(args)
    if rc != 0:
        msg = (err or out).strip() or f"uwfmgr 返回码 {rc}"
        raise UWFError(f"操作失败 [{ ' '.join(args) }]: {msg}")
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
        vols = self._all("UWF_Volume")
        result = []
        for v in vols:
            entry = {}
            for prop in ("CurrentSession", "DriveLetter", "Protected",
                         "BindByDriveLetter", "CommitPending", "VolumeName"):
                try:
                    entry[prop] = _variant_to_py(getattr(v, prop))
                except Exception:
                    entry[prop] = None
            if entry.get("Protected") is None:
                entry["Protected"] = False
            result.append(entry)
        return result

    def get_overlay_config(self):
        c = self._first("UWF_OverlayConfig")
        if c is None:
            return {"Type": None, "MaximumSize": None}
        out = {}
        for prop in ("Type", "MaximumSize"):
            try:
                out[prop] = _variant_to_py(getattr(c, prop))
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
        直接 v.GetExclusions() 在 win32com 下会被误解析。"""
        vols = self._all("UWF_Volume")
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
                        if fname:
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
        """0=基于内存(RAM), 1=基于磁盘(Disk)。"""
        kind = "RAM" if int(overlay_type) == 0 else "Disk"
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
        return []
