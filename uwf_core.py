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
UWF_CORE_VERSION = "2.23"


UWF_FEATURE_NAME = "Client-UnifiedWriteFilter"

_UWF_EXE_CANDIDATES = (
    r"C:\Windows\System32\uwfmgr.exe",
    r"C:\Windows\Sysnative\uwfmgr.exe",
    r"C:\Windows\SysWOW64\uwfmgr.exe",
)

# UWF 仅在这些 Windows 版本上提供（EditionID 片段匹配）
_UWF_EDITION_KEYS = (
    "Enterprise",       # 企业版 / 企业版 LTSC
    "Education",        # 教育版
    "IoTEnterprise",    # IoT 企业版
    "ProfessionalEducation",
    "ProfessionalWorkstation",
)


def _find_uwfmgr():
    """返回真实存在的 uwfmgr.exe 路径，不存在返回 None。"""
    for p in _UWF_EXE_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _resolve_uwfmgr():
    return _find_uwfmgr() or _UWF_EXE_CANDIDATES[0]


def _decode(raw):
    """Windows 控制台输出可能是 GBK 或 UTF-8，逐个尝试解码。"""
    if isinstance(raw, str):
        return raw
    for enc in ("gbk", "utf-8", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def get_windows_edition():
    """返回 (产品名, EditionID)，如 ("Windows 10 Enterprise", "Enterprise")。"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            try:
                product = winreg.QueryValueEx(k, "ProductName")[0]
            except Exception:
                product = "未知"
            try:
                edition = winreg.QueryValueEx(k, "EditionID")[0]
            except Exception:
                edition = ""
            return product, edition
    except Exception:
        return "未知", ""


def edition_supports_uwf(edition_id=""):
    """该 Windows 版本是否提供 UWF 功能。未知版本返回 True（不误报）。"""
    if not edition_id:
        return True
    return any(k.lower() in edition_id.lower() for k in _UWF_EDITION_KEYS)


def uwf_availability():
    """诊断本机 UWF 功能可用性（GUI 无关，可单测）。

    返回 dict：
      available      : uwfmgr.exe 是否存在（=功能已安装）
      supported      : 当前 Windows 版本是否提供 UWF
      path           : uwfmgr.exe 路径或 None
      product/edition: Windows 版本信息
      state          : 'ok' | 'not_installed' | 'unsupported'
      msg            : 面向用户的中文说明
    """
    path = _find_uwfmgr()
    product, edition = get_windows_edition()
    supported = edition_supports_uwf(edition)
    if path:
        state, msg = "ok", "UWF 功能已安装，可正常使用。"
    elif not supported:
        state = "unsupported"
        msg = (f"当前系统「{product}」（{edition or '未知版本'}）不提供 UWF 功能。\n"
               "UWF 仅适用于 Windows 企业版 / 教育版 / IoT 企业版；\n"
               "家庭版、专业版、工作站专业版等无法启用，需更换系统版本。")
    else:
        state = "not_installed"
        msg = ("本机尚未安装 UWF 功能（缺少 uwfmgr.exe）。\n"
               "UWF 是 Windows 的「可选功能」，需先启用并重启电脑才会出现。")
    return {"available": bool(path), "supported": supported, "path": path,
            "product": product, "edition": edition, "state": state, "msg": msg}


def enable_uwf_feature(timeout=300):
    """通过 DISM 启用 UWF 可选功能（需管理员）。返回 (returncode, output)。

    注意：启用后必须重启电脑，重启后 uwfmgr.exe 才会出现。
    DISM 不可用时自动回退到 PowerShell Enable-WindowsOptionalFeature。
    """
    cmd = ["dism", "/online", "/enable-feature",
           f"/featurename:{UWF_FEATURE_NAME}", "/all", "/norestart"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              shell=False)
    except FileNotFoundError:
        cmd = ["powershell", "-NoProfile", "-Command",
               f"Enable-WindowsOptionalFeature -Online "
               f"-FeatureName {UWF_FEATURE_NAME} -All -NoRestart"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  shell=False)
        except Exception as e:
            return -1, f"启用失败：{e}"
    except subprocess.TimeoutExpired:
        return -1, f"操作超时（>{timeout} 秒）。"
    except Exception as e:
        return -1, f"启用失败：{e}"
    out = _decode((proc.stdout or b"") + (proc.stderr or b""))
    return proc.returncode, out


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


# ==================== 磁盘 / 覆盖文件位置 ====================

OVERLAY_FILE_NAME = "uwfswap.sys"

# MSFT_PhysicalDisk.MediaType
_MEDIA_TYPE = {0: "未知", 3: "HDD 机械盘", 4: "SSD 固态盘", 5: "SCM 存储级内存"}
# MSFT_PhysicalDisk.BusType（常用值）
_BUS_TYPE = {0: "未知", 1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA",
             6: "光纤", 7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS",
             11: "SATA", 12: "SD", 13: "MMC", 14: "虚拟", 15: "文件支持",
             16: "存储空间", 17: "NVMe", 18: "SCM", 19: "UFS"}
# 傲腾（Optane / 3D XPoint）型号关键字
_OPTANE_KEYS = ("OPTANE", "MEMPEK", "P4800", "P1600", "P5800")


def _fixed_drive_letters():
    """返回本机固定磁盘盘符列表（大写，如 ['C:', 'D:', 'F:']）。

    注意：pywin32 里没有 win32api.GetDriveType，必须走 kernel32.GetDriveTypeW，
    DRIVE_FIXED == 3（可移动盘不适合放 UWF 覆盖文件）。
    """
    letters = []
    get_type = ctypes.windll.kernel32.GetDriveTypeW
    try:
        raw = win32api.GetLogicalDriveStrings()
    except Exception:
        raw = ""
    for d in (raw or "").split("\x00"):
        d = (d or "").strip()
        if len(d) >= 2 and d[1] == ":":
            try:
                if get_type(d + "\\") == 3:          # DRIVE_FIXED
                    letters.append(d[0].upper() + ":")
            except Exception:
                continue
    if not letters:                                   # 兜底
        for ch in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            try:
                if os.path.isdir(ch + ":\\"):
                    letters.append(ch + ":")
            except Exception:
                continue
    return sorted(set(letters))


def find_overlay_files():
    """扫描所有固定盘根目录，返回 {盘符(大写): 大小MB}，只含已存在项。

    UWF 磁盘模式的覆盖文件固定名为 uwfswap.sys，放在所选卷的根目录。
    """
    found = {}
    for letter in _fixed_drive_letters():
        p = f"{letter}\\{OVERLAY_FILE_NAME}"
        try:
            if os.path.isfile(p):
                found[letter] = os.path.getsize(p) / (1024.0 * 1024.0)
        except Exception:
            continue
    return found


def _wmi_volumes():
    """盘符 -> {label, fs, size_gb, free_gb}（仅固定磁盘）。"""
    out = {}
    try:
        svc = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        for d in svc.InstancesOf("Win32_LogicalDisk"):
            try:
                if int(getattr(d, "DriveType", 0) or 0) != 3:
                    continue
                dl = str(getattr(d, "DeviceID", "") or "").upper()
                if len(dl) != 2:
                    continue
                out[dl] = {
                    "label": getattr(d, "VolumeName", "") or "",
                    "fs": getattr(d, "FileSystem", "") or "",
                    "size_gb": float(getattr(d, "Size", 0) or 0) / (1024.0 ** 3),
                    "free_gb": float(getattr(d, "FreeSpace", 0) or 0) / (1024.0 ** 3),
                }
            except Exception:
                continue
    except Exception:
        pass
    return out


def _wmi_partition_disk():
    """盘符 -> 物理磁盘号（依赖 root\\Microsoft\\Windows\\Storage）。

    坑：win32com 读 MSFT_Partition.DriveLetter 得到的是「字符编码整数」
    （C=67, D=68, E=69, F=70, 无盘符=0），不是字符串，必须 chr() 还原。
    """
    out = {}
    try:
        svc = win32com.client.GetObject(
            r"winmgmts:\\.\root\Microsoft\Windows\Storage")
        for p in svc.InstancesOf("MSFT_Partition"):
            try:
                dl = getattr(p, "DriveLetter", None)
                dn = getattr(p, "DiskNumber", None)
                if dl is None or dn is None:
                    continue
                if isinstance(dl, (int, float)) or str(dl).isdigit():
                    code = int(dl)
                    if code <= 0:
                        continue                 # 无盘符分区
                    letter = chr(code)
                else:
                    letter = str(dl)[0]
                if not letter.isalpha():
                    continue
                out[letter.upper() + ":"] = int(dn)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _wmi_disks():
    """物理磁盘号 -> {model, media_type, bus_type}（root\\...\\Storage）。"""
    out = {}
    try:
        svc = win32com.client.GetObject(
            r"winmgmts:\\.\root\Microsoft\Windows\Storage")
        for p in svc.InstancesOf("MSFT_PhysicalDisk"):
            try:
                dn = int(getattr(p, "DeviceId"))
                out[dn] = {
                    "model": getattr(p, "FriendlyName", "") or "",
                    "media_type": int(getattr(p, "MediaType", 0) or 0),
                    "bus_type": int(getattr(p, "BusType", 0) or 0),
                }
            except Exception:
                continue
    except Exception:
        pass
    return out


def _cimv2_partition_disk():
    """兜底：Win32_LogicalDiskToPartition 关联得到 盘符 -> 物理磁盘号。"""
    out = {}
    try:
        svc = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        for a in svc.InstancesOf("Win32_LogicalDiskToPartition"):
            try:
                letter = str(a.Dependent.DeviceID or "").upper()[:2]
                disk_no = int(a.Antecedent.DiskIndex)
                if len(letter) == 2 and letter[1] == ":" and disk_no >= 0:
                    out[letter] = disk_no
            except Exception:
                continue
    except Exception:
        pass
    return out


def _cimv2_disks():
    """兜底：Win32_DiskDrive -> {磁盘号: {model, media_type, bus_type}}。"""
    media_by_if = {"SCSI": 4, "NVMe": 4, "IDE": 0, "USB": 0}
    bus_by_if = {"SCSI": 1, "NVMe": 17, "IDE": 3, "USB": 7}
    out = {}
    try:
        svc = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        for d in svc.InstancesOf("Win32_DiskDrive"):
            try:
                idx = int(getattr(d, "Index"))
                iface = str(getattr(d, "InterfaceType", "") or "").upper()
                out[idx] = {
                    "model": getattr(d, "Model", "") or "",
                    "media_type": media_by_if.get(iface, 0),
                    "bus_type": bus_by_if.get(iface, 0),
                }
            except Exception:
                continue
    except Exception:
        pass
    return out


def list_storage():
    """枚举所有固定盘的卷，并附带所在物理磁盘信息（供「选择覆盖磁盘」用）。

    返回 list[dict]，按盘符排序，字段：
        letter    盘符，如 "C:"
        label     卷标
        fs        文件系统
        size_gb   总容量(GB)
        free_gb   可用空间(GB)
        disk_no   物理磁盘号（未知为 None）
        model     物理磁盘型号
        media     介质类型描述（SSD / HDD / 傲腾 Optane）
        is_optane 是否傲腾
        bus       总线类型名
        swap_mb   该卷根目录已有 uwfswap.sys 大小(MB)，无则 0
    """
    vols = _wmi_volumes()
    part2disk = _wmi_partition_disk() or _cimv2_partition_disk()
    disks = _wmi_disks() or _cimv2_disks()
    swaps = find_overlay_files()

    rows = []
    for letter in sorted(vols):
        info = vols[letter] or {}
        dno = part2disk.get(letter)
        dinfo = disks.get(dno, {}) if dno is not None else {}
        media_type = int(dinfo.get("media_type") or 0)
        model = dinfo.get("model") or ""
        is_opt = (media_type == 5 or
                  any(k in model.upper() for k in _OPTANE_KEYS))
        media = "傲腾 Optane" if is_opt else _MEDIA_TYPE.get(media_type, "未知")
        rows.append({
            "letter": letter,
            "label": info.get("label") or "",
            "fs": info.get("fs") or "",
            "size_gb": info.get("size_gb", 0.0),
            "free_gb": info.get("free_gb", 0.0),
            "disk_no": dno,
            "model": model,
            "media": media,
            "is_optane": is_opt,
            "bus": _BUS_TYPE.get(int(dinfo.get("bus_type") or 0), "未知"),
            "swap_mb": float(swaps.get(letter, 0) or 0),
        })
    return rows


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

    def create_swapfile(self, volume):
        """在指定卷上创建覆盖交换文件（uwfswap.sys），并把覆盖类型设为磁盘。

        对应 `uwfmgr volume create-swapfile <卷>`。这是 UWF 官方提供的
        「指定磁盘模式覆盖文件放在哪个盘」的命令：
            Allow UWF swapfile (aka. DISK Overlay) to be created and used
            on any volume —— 覆盖文件可放在任意卷，与该卷是否受保护无关。

        约束（由 UWF 强制）：筛选器必须处于禁用状态，且覆盖类型为磁盘模式。
        重启后生效。
        """
        _cli(["volume", "create-swapfile", _norm_drive(volume)])
        return True

    def get_overlay_file_locations(self):
        """返回各盘根目录已有的 uwfswap.sys：{盘符: 大小MB}。"""
        return find_overlay_files()

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

    # ==================== 批量操作（避免逐个弹 UAC）====================

    def batch_run(self, commands):
        """批量执行 uwfmgr 命令（每条为参数列表）。

        管理员环境：直接逐条 _run_direct 执行（无 UAC 弹窗）。
        非管理员环境：把所有命令合并成**一条** `cmd /c "uwfmgr ... & uwfmgr ..."`，
        通过 runas 仅弹**一次** UAC；由于父进程已提升，子进程 uwfmgr
        继承提升令牌，不再逐个弹窗。这样清理时可一次性固化几十个目录的删除。
        """
        if not commands:
            return
        if _is_admin():
            for args in commands:
                try:
                    _run_direct(args)
                except Exception:
                    # 单条失败不影响其余（如某目录不在覆盖层中）
                    pass
            return
        global UWFMGR
        if UWFMGR is None:
            UWFMGR = _resolve_uwfmgr()
        parts = []
        for args in commands:
            q = " ".join(f'"{a}"' for a in args)
            parts.append(f'"{UWFMGR}" {q}')
        line = " & ".join(parts)
        try:
            info = win32api.ShellExecuteEx(
                fMask=win32con.SEE_MASK_NOCLOSEPROCESS,
                hwnd=0, lpVerb="runas", lpFile="cmd.exe",
                lpParameters=f'/c "{line}"', nShow=0)
        except Exception as e:
            raise UWFError(f"无法请求管理员权限执行 UWF 操作: {e}")
        hproc = info.get("hProcess")
        if hproc:
            try:
                win32event.WaitForSingleObject(hproc, 60000)
            except Exception:
                pass

    def batch_commit(self, commit_dirs, commit_files):
        """批量提交删除：把若干目录/文件的删除固化到物理盘。

        commit_dirs: 目录完整路径列表（提交该目录树内所有删除记录）
        commit_files: 单文件完整路径列表（commit-delete）
        """
        cmds = []
        for d in sorted(set(commit_dirs or [])):
            cmds.append(["file", "commit", _full_path("c", d)])
        for f in sorted(set(commit_files or [])):
            cmds.append(["file", "commit-delete", _full_path("c", f)])
        if cmds:
            self.batch_run(cmds)

    def batch_exclude(self, drive_letter, paths):
        """批量加入 UWF 排除列表（目录或文件均可）。"""
        cmds = []
        for p in sorted(set(paths or [])):
            cmds.append(["file", "add-exclusion", _full_path(drive_letter, p)])
        if cmds:
            self.batch_run(cmds)

    # ==================== HORM（CLI）====================

    def enable_horm(self):
        _cli(["filter", "enable-HORM"])
        return True

    def disable_horm(self):
        _cli(["filter", "disable-HORM"])
        return True

    # ==================== 重启/关机（Windows shutdown.exe）====================
    # 注意：uwfmgr.exe 没有 restart/shutdown 命令，必须用系统 shutdown.exe

    def restart_system(self):
        """立即重启计算机。"""
        import os
        r = subprocess.run(
            [os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                          "System32", "shutdown.exe"),
             "/r", "/t", "0"],
            capture_output=True, timeout=10)
        if r.returncode not in (0, 1115):  # 1115=关机已在进行中(正常)
            raise UWFError(f"重启失败: shutdown 返回码 {r.returncode}")
        return True

    def shutdown_system(self):
        """立即关闭计算机。"""
        import os
        r = subprocess.run(
            [os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                          "System32", "shutdown.exe"),
             "/s", "/t", "0"],
            capture_output=True, timeout=10)
        if r.returncode not in (0, 1115):
            raise UWFError(f"关机失败: shutdown 返回码 {r.returncode}")
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
