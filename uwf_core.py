"""
UWF Manager Pro v2.0 - 核心 WMI 封装模块
通过 win32com 访问 root\\standardcimv2\\embedded 命名空间。
该模块只负责与系统交互，不包含任何 UI 逻辑。

支持的 WMI 类：
  UWF_Filter    - 启用/禁用/HORM/重启/关机
  UWF_Volume    - 分区保护/提交文件/排除列表
  UWF_OverlayConfig - 覆盖类型/最大缓存
  UWF_Overlay   - 阈值设置/覆盖使用量
"""
import sys
import win32com.client


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

    # ---------- 实例查找 ----------
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

    def _find_volume(self, drive_letter):
        """按盘符找到对应的 UWF_Volume 实例。"""
        self._require_conn()
        try:
            items = self._wmi.InstancesOf("UWF_Volume")
            for v in items:
                if getattr(v, "DriveLetter", None) == drive_letter:
                    return v
        except Exception:
            pass
        return None

    # ==================== 状态查询 ====================

    def get_filter(self):
        """返回 UWF_Filter 状态 dict。"""
        f = self._first("UWF_Filter")
        if f is None:
            return {
                "CurrentEnabled": False,
                "NextEnabled": False,
                "ShutdownPending": False,
                "HORMEnabled": False,
            }
        out = {}
        for prop in ("CurrentEnabled", "NextEnabled", "ShutdownPending",
                     "HORMEnabled"):
            try:
                out[prop] = _variant_to_py(getattr(f, prop))
            except Exception:
                out[prop] = None
        return out

    def get_volumes(self):
        """返回所有卷列表。每个元素是 dict（含 Protected/DriveLetter 等）。"""
        vols = self._all("UWF_Volume")
        result = []
        for v in vols:
            entry = {}
            for prop in ("CurrentSession", "DriveLetter", "Protected",
                         "BindByDriveLetter", "CommitPending",
                         "VolumeName"):
                try:
                    entry[prop] = _variant_to_py(getattr(v, prop))
                except Exception:
                    entry[prop] = None
            if entry.get("Protected") is None:
                entry["Protected"] = False
            result.append(entry)
        return result

    def get_overlay_config(self):
        """返回 UWF_OverlayConfig 配置 dict。"""
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
        """返回 UWF_Overlay 使用信息 dict。
        关键字段: OverlayConsumption(MB), AvailableSpace(MB),
                  CriticalOverlayThreshold(MB), WarningOverlayThreshold(MB)
        """
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
        """返回排除列表。
        drive_letter: 指定盘符(如 'C:') 则只返回该卷的排除项；
                      None 则返回所有卷的排除项。
        返回 list of dict: [{"drive": "C:", "path": "\\path\\to\\file"}, ...]

        注意：必须用 ExecMethod_("GetExclusions") 而非直接调用
              v.GetExclusions()，因为 win32com 会将后者错误解析为 int 属性。
        """
        vols = self._all("UWF_Volume")
        results = []
        for v in vols:
            dl = getattr(v, "DriveLetter", None)
            if drive_letter and dl != drive_letter:
                continue
            try:
                result = v.ExecMethod_("GetExclusions")
                # 返回值: ExcludedFiles (COM对象列表) + ReturnValue (int)
                excl_list = getattr(result, "ExcludedFiles", None)
                if excl_list:
                    for item in excl_list:
                        fname = getattr(item, "FileName", None)
                        if fname:
                            results.append({"drive": dl, "path": str(fname)})
            except Exception:
                pass
        return results

    # ==================== 写入过滤操作 ====================

    def enable_filter(self):
        """启用写入过滤（下次重启生效）。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.Enable()
            return True
        except Exception as e:
            raise UWFError(f"启用写入过滤失败: {e}") from e

    def disable_filter(self):
        """禁用写入过滤（下次重启生效）。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.Disable()
            return True
        except Exception as e:
            raise UWFError(f"禁用写入过滤失败: {e}") from e

    # ==================== 覆盖配置操作 ====================

    def set_overlay_type(self, overlay_type):
        """设置覆盖类型。0=基于内存, 1=基于磁盘。"""
        c = self._first("UWF_OverlayConfig")
        if c is None:
            raise UWFError("无法获取 UWF_OverlayConfig")
        try:
            c.SetType(int(overlay_type))
            return True
        except Exception as e:
            raise UWFError(f"设置覆盖类型失败: {e}") from e

    def set_maximum_size(self, size_mb):
        """设置覆盖层最大大小（MB）。"""
        c = self._first("UWF_OverlayConfig")
        if c is None:
            raise UWFError("无法获取 UWF_OverlayConfig")
        try:
            c.SetMaximumSize(int(size_mb))
            return True
        except Exception as e:
            raise UWFError(f"设置最大缓存失败: {e}") from e

    def set_warning_threshold(self, size_mb):
        """设置警告阈值（MB）。"""
        o = self._first("UWF_Overlay")
        if o is None:
            raise UWFError("无法获取 UWF_Overlay")
        try:
            o.SetWarningThreshold(int(size_mb))
            return True
        except Exception as e:
            raise UWFError(f"设置警告阈值失败: {e}") from e

    def set_critical_threshold(self, size_mb):
        """设置严重阈值（MB）。"""
        o = self._first("UWF_Overlay")
        if o is None:
            raise UWFError("无法获取 UWF_Overlay")
        try:
            o.SetCriticalThreshold(int(size_mb))
            return True
        except Exception as e:
            raise UWFError(f"设置严重阈值失败: {e}") from e

    # ==================== 分区保护操作 ====================

    def protect_volume(self, drive_letter, current_session=True):
        """保护指定分区。
        current_session=True: 当前会话立即生效
        current_session=False: 下次重启生效
        """
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            if current_session:
                v.Protect()
            else:
                # Protect() 同时影响当前和下次；通过 NextEnabled 控制"下次"
                v.Protect()
            return True
        except Exception as e:
            raise UWFError(f"保护卷 {drive_letter} 失败: {e}") from e

    def unprotect_volume(self, drive_letter, current_session=True):
        """取消保护指定分区。"""
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.Unprotect()
            return True
        except Exception as e:
            raise UWFError(f"取消保护卷 {drive_letter} 失败: {e}") from e

    # ==================== 排除列表操作 ====================

    def add_exclusion(self, drive_letter, file_path):
        """添加排除路径到指定卷。
        file_path: 相对于卷根的路径，如 '\\Users\\YourName\\.codex'
        """
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.AddExclusion(file_path)
            return True
        except Exception as e:
            raise UWFError(f"添加排除失败: {e}") from e

    def remove_exclusion(self, drive_letter, file_path):
        """从指定卷移除排除路径。"""
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.RemoveExclusion(file_path)
            return True
        except Exception as e:
            raise UWFError(f"移除排除失败: {e}") from e

    def remove_all_exclusions(self, drive_letter):
        """清除指定卷的所有排除项。"""
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.RemoveAllExclusions()
            return True
        except Exception as e:
            raise UWFError(f"清除排除列表失败: {e}") from e

    # ==================== 文件/注册表提交操作 ====================

    def commit_file(self, drive_letter, file_path):
        """提交指定文件更改到底层存储（穿透覆盖层）。"""
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.CommitFile(file_path)
            return True
        except Exception as e:
            raise UWFError(f"提交文件失败: {e}") from e

    def commit_file_deletion(self, drive_letter, file_path):
        """提交文件删除（不计入覆盖层）。"""
        v = self._find_volume(drive_letter)
        if v is None:
            raise UWFError(f"找不到卷 {drive_letter}")
        try:
            v.CommitFileDeletion(file_path)
            return True
        except Exception as e:
            raise UWFError(f"提交文件删除失败: {e}") from e

    def commit_all_deletions(self):
        """提交所有删除操作。"""
        v = self._first("UWF_Volume")
        if v is None:
            raise UWFError("无法获取 UWF_Volume")
        try:
            v.CommitAllDeletions()
            return True
        except Exception as e:
            raise UWFError(f"提交所有删除失败: {e}") from e

    # ==================== HORM 操作 ====================

    def enable_horm(self):
        """启用 HORM (Hibernate Once Resume Many)。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.EnableHORM()
            return True
        except Exception as e:
            raise UWFError(f"启用 HORM 失败: {e}") from e

    def disable_horm(self):
        """禁用 HORM。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.DisableHORM()
            return True
        except Exception as e:
            raise UWFError(f"禁用 HORM 失败: {e}") from e

    # ==================== 重启/关机 ====================

    def restart_system(self):
        """重启系统（应用 UWF 设置变更）。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.RestartSystem()
            return True
        except Exception as e:
            raise UWFError(f"重启失败: {e}") from e

    def shutdown_system(self):
        """关闭系统。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.ShutdownSystem()
            return True
        except Exception as e:
            raise UWFError(f"关机失败: {e}") from e

    # ==================== 重置设置 ====================

    def reset_settings(self):
        """重置所有 UWF 设置为默认值。"""
        f = self._first("UWF_Filter")
        if f is None:
            raise UWFError("无法获取 UWF_Filter")
        try:
            f.ResetSettings()
            return True
        except Exception as e:
            raise UWFError(f"重置设置失败: {e}") from e

    # ==================== 覆盖文件查询 ====================

    def get_overlay_files(self, drive_letter):
        """获取指定卷上被覆盖层缓存的文件列表。
        返回 list of str（完整 UNC 路径）。
        """
        o = self._first("UWF_Overlay")
        if o is None:
            return []
        try:
            files = o.GetOverlayFiles(drive_letter)
            if files:
                return [str(f) for f in files]
        except Exception:
            pass
        return []
