"""
UWF Manager Pro - 覆盖层实时写入监控模块

用 ReadDirectoryChangesW 实时捕获受保护卷（默认 C:）上的文件写入事件。
在 UWF 启用时，所有对受保护卷的写入都会进入覆盖层，因此监控真实文件系统
即可等价于"监控进来了什么文件到内存/覆盖层"。

本模块只负责采集事件，不触碰任何 UI。
"""
import os
import time
import queue
import threading
import win32file
import win32con

# 事件动作 -> 中文（使用标准 Win32 整数常量，避免依赖 win32con 命名）
FILE_ACTION_ADDED = 1
FILE_ACTION_REMOVED = 2
FILE_ACTION_MODIFIED = 3
FILE_ACTION_RENAMED_OLD_NAME = 4
FILE_ACTION_RENAMED_NEW_NAME = 5
# CreateFile 访问权限（win32con 缺此命名，用标准值）
FILE_LIST_DIRECTORY = 0x0001
_ACTIONS = {
    FILE_ACTION_ADDED: "新增",
    FILE_ACTION_REMOVED: "删除",
    FILE_ACTION_MODIFIED: "修改",
    FILE_ACTION_RENAMED_OLD_NAME: "改名(旧)",
    FILE_ACTION_RENAMED_NEW_NAME: "改名(新)",
}


def _action_name(action):
    return _ACTIONS.get(action, f"事件{action}")


class OverlayMonitor:
    """实时目录变更监控器。"""

    def __init__(self):
        self._running = False
        self._threads = []
        self._queue = queue.Queue()
        self._watched = []
        self._lock = threading.Lock()

    @property
    def running(self):
        return self._running

    @property
    def watched_dirs(self):
        return list(self._watched)

    def start(self, dirs, buffer_size=65536):
        """开始监控一组目录（递归）。dirs: list of str。"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._watched = []
            for d in dirs:
                if os.path.isdir(d):
                    self._watched.append(d)
                    t = threading.Thread(
                        target=self._watch_loop,
                        args=(d, buffer_size),
                        daemon=True,
                        name=f"mon-{d}")
                    t.start()
                    self._threads.append(t)

    def stop(self):
        """停止所有监控线程。"""
        with self._lock:
            self._running = False
            self._threads = []

    def _watch_loop(self, path, buffer_size):
        try:
            h = win32file.CreateFile(
                path,
                FILE_LIST_DIRECTORY,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE |
                win32con.FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_FLAG_BACKUP_SEMANTICS,
                None,
            )
        except Exception:
            return
        notify_filter = (
            win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
            win32con.FILE_NOTIFY_CHANGE_DIR_NAME |
            win32con.FILE_NOTIFY_CHANGE_SIZE |
            win32con.FILE_NOTIFY_CHANGE_LAST_WRITE
        )
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                changes = win32file.ReadDirectoryChangesW(
                    h, buffer_size, True, notify_filter, None, None)
            except Exception:
                # 句柄失效或目录被删除
                break
            if not changes:
                continue
            for action, fname in changes:
                full = os.path.join(path, fname)
                act = _action_name(action)
                size = 0
                try:
                    if os.path.isfile(full):
                        size = os.path.getsize(full)
                except Exception:
                    size = 0
                self._queue.put((time.time(), full, act, size))
        try:
            win32file.CloseHandle(h)
        except Exception:
            pass

    def drain(self, max_items=500):
        """取出队列中最多 max_items 个事件。返回 list of (ts, path, action, size)。"""
        out = []
        while len(out) < max_items:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out
