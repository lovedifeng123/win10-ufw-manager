"""
UWF Manager Pro - 核心 WMI 封装模块
通过 win32com 访问 root\\standardcimv2\\embedded 命名空间。
该模块只负责与系统交互，不包含任何 UI 逻辑。
"""
import sys
import win32com.client
from win32com.client import constants


class UWFError(Exception):
    """UWF 相关异常基类"""
    pass


class UWFNotSupported(UWFError):
    """当前系统不支持 UWF（通常因为没有 embedded 命名空间）"""
    pass


def _get_wmi(namespace="root\\standardcimv2\\embedded"):
    """获取指定命名空间的 WMI 连接，失败抛出 UWFNotSupported"""
    try:
        return win32com.client.GetObject(f"winmgmts:\\\\.\\{namespace}")
    except Exception as e:
        raise UWFNotSupported(f"无法连接到 {namespace}: {e}") from e


def _variant_to_py(val):
    """把 COM 返回值转成 Python 原生类型（容错处理 NULL）"""
    if val is None:
        return None
    return val


class UWFCore:
    """UWF 功能核心封装。所有方法在异常时抛出 UWFError。"""

    def __init__(self):
        self._wmi = None
        self._connected = False

    # ---------- 连接管理 ----------
    def connect(self):
        """建立 WMI 连接。需要管理员权限。"""
        try:
            self._wmi = _get_wmi()
            self._connected = True
        except UWFNotSupported:
            self._connected = False
            raise
        return True

    @property
    def connected(self):
        return self._connected

    def _require_conn(self):
        if not self._connected:
            self.connect()

    # ---------- 读取类实例 ----------
    def _first(self, class_name):
        """返回某类的第一个实例（UWF 类通常为单例）。无则返回 None。"""
        self._require_conn()
        try:
            items = self._wmi.InstancesOf(class_name)
            for it in items:
                return it
        except Exception:
            return None
        return None

    def _all(self, class_name):
        self._require_conn()
        result = []
        try:
            items = self._wmi.InstancesOf(class_name)
            for it in items:
                result.append(it)
        except Exception:
            pass
        return result

    # ---------- 状态查询 ----------
    def get_filter(self):
        """返回 UWF_Filter 状态 dict。"""
        f = self._first("UWF_Filter")
        if f is None:
            return {
                "CurrentEnabled": False,
                "ShutdownPending": False,
                "HORMEnabled": False,
                "NextEnabled": None,
                "CurrentMode": "Unknown",
            }
        out = {}
        for prop in ("CurrentEnabled", "ShutdownPending", "HORMEnabled",
                     "NextEnabled"):
            try:
                out[prop] = _variant_to_py(getattr(f, prop))
            except Exception:
                out[prop] = None
        out["CurrentMode"] = "Unknown"
        return out

    def get_volumes(self):
        """返回受保护卷列表。每个元素是 dict。"""
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
            # 容错：Protected 字段在未保护卷上可能为 NULL
            if entry.get("Protected") is None:
                entry["Protected"] = False
            result.append(entry)
        return result

    def get_overlay_config(self):
        """返回 UWF_OverlayConfig 配置。"""
        c = self._first("UWF_OverlayConfig")
        if c is None:
            return {"Type": None, "MaximumSize": None, "OverlayDrive": None}
        out = {}
        for prop in ("Type", "MaximumSize", "OverlayDrive"):
            try:
                out[prop] = _variant_to_py(getattr(c, prop))
            except Exception:
                out[prop] = None
        return out

    def get_overlay(self):
        """返回 UWF_Overlay 使用信息（真实属性名见下方）。"""
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

    # ---------- 操作 ----------
    def enable(self):
        """启用 UWF（下次重启生效）。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter 实例")
        try:
            f.Enable()
            return True
        except Exception as e:
            raise UWFError(f"启用 UWF 失败: {e}") from e

    def disable(self):
        """禁用 UWF（下次重启生效）。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter 实例")
        try:
            f.Disable()
            return True
        except Exception as e:
            raise UWFError(f"禁用 UWF 失败: {e}") from e

    def commit_file_deletion(self, drive, path):
        """提交文件删除（不计入覆盖层）。path 为相对卷根路径。"""
        v = self._first("UWF_Volume")
        if v is None:
            raise UWFError("无法获取 UWF_Volume 实例")
        try:
            v.CommitFileDeletion(drive, path)
            return True
        except Exception as e:
            raise UWFError(f"提交文件删除失败: {e}") from e

    def commit_all_deletions(self):
        """提交所有删除操作（清空覆盖层中的删除记录）。"""
        v = self._first("UWF_Volume")
        if v is None:
            raise UWFError("无法获取 UWF_Volume 实例")
        try:
            v.CommitAllDeletions()
            return True
        except Exception as e:
            raise UWFError(f"提交所有删除失败: {e}") from e

    def set_overlay_maximum(self, size_mb):
        """设置覆盖层最大大小（MB）。"""
        c = self._first("UWF_OverlayConfig")
        if c is None:
            raise UWFError("无法获取 UWF_OverlayConfig 实例")
        try:
            c.MaximumSize = size_mb
            return True
        except Exception as e:
            raise UWFError(f"设置覆盖层大小失败: {e}") from e
