"""
UWF Manager Pro v2.10 - 主程序（tkinter UI）
功能：
  1. 状态面板：启用/禁用/HORM/关机待处理
  2. 覆盖层内存监控（已用/总容量/阈值变色）← 修复数据显示
  3. 受保护卷列表
  4. 基本设置：写入过滤 / 覆盖类型
  5. 缓存设置：最大缓存 / 警告阈值 / 严重阈值
  6. 分区保护表格（当前状态 + 重启状态，保护/不保护操作）
  7. 排除列表管理（添加/删除路径，导入/导出）
  8. 文件浏览器（覆盖层占用来源分析）
  9. 覆盖层文件日志（内存中的文件）
 10. 操作：启用/禁用/提交/重启/关机
 11. 系统托盘图标（右下角显示 UWF 状态）

关键规则：所有 WMI/COM 访问在带 CoInitialize 的独立线程中执行。
"""
import sys
import os
import time
import threading
import pythoncom
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import uwf_core
import file_scan
import overlay_monitor
import ctypes
import win32gui
import win32api
import win32con

# ==================== 常量与主题 ====================
ACCENT = "#0078D4"
ACCENT_DARK = "#005A9E"
BG = "#F3F3F3"
CARD_BG = "#FFFFFF"
TEXT = "#1A1A1A"
TEXT_SUB = "#666666"
GREEN = "#107C10"
RED = "#D13438"
AMBER = "#FF8C00"
FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 16, "bold")
FONT_BIG = ("Segoe UI", 24, "bold")

# 覆盖类型映射
OVERLAY_TYPES = {0: "基于内存", 1: "基于磁盘"}
OVERLAY_TYPES_REV = {"基于内存": 0, "基于磁盘": 1}


def human_size(n):
    """把字节数格式化为可读字符串。"""
    try:
        n = int(n)
    except Exception:
        n = 0
    if n >= 1024 * 1024 * 1024:
        return f"{n / 1024 / 1024 / 1024:.2f} GB"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def boot_time_epoch():
    """返回本次开机时间（epoch 秒）。"""
    try:
        return time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000.0
    except Exception:
        return time.time() - 86400


# ==================== 托盘图标类（真实系统托盘 + 动态数字图标）====================
class SystemTrayIcon:
    """基于 win32gui 的真实系统托盘图标。图标动态显示 UWF 剩余内存数值。"""

    WM_TRAY = win32con.WM_USER + 20

    # 图标配色（参考用户截图：深色底 + 黄色数字）
    ICON_BG = (45, 45, 45)       # #2D2D2D 深灰底
    ICON_FG_NORMAL = (230, 184, 0)   # #E6B800 金黄（正常）
    ICON_FG_WARN = (255, 80, 60)     # #FF503C 红色（<20% 剩余）
    ICON_SIZE = 64                  # 64x64 高清，系统自动缩放

    def __init__(self, parent_app):
        self.app = parent_app
        self.visible = False
        self.hwnd = None
        self.hicon = None
        self._last_text = ""          # 避免重复渲染相同图标
        self._init()

    def _init(self):
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "UWFManagerProTray"
        wc.lpfnWndProc = self._wndproc
        wc.style = win32con.CS_VREDRAW | win32con.CS_HREDRAW
        wc.hCursor = win32api.LoadCursor(0, win32con.IDC_ARROW)
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        try:
            self.atom = win32gui.RegisterClass(wc)
        except Exception:
            self.atom = wc.lpszClassName  # 已注册则直接用类名
        self.hwnd = win32gui.CreateWindow(
            self.atom, "UWFManagerProTray", 0, 0, 0, 0, 0, 0, 0,
            wc.hInstance, None)
        # 默认图标（PIL 未就绪时使用）
        self.hicon = self._make_default_icon()
        self.visible = True
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        nid = (self.hwnd, 0, flags, self.WM_TRAY, self.hicon,
               "UWF Manager Pro")
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)

    def _make_default_icon(self):
        """生成默认图标（显示 "--" 占位）。"""
        try:
            return self._create_hicon_from_text("--")
        except Exception:
            return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_TRAY:
            if lparam == win32con.WM_LBUTTONDBLCLK:
                self.restore()
            elif lparam == win32con.WM_RBUTTONUP:
                self._show_menu()
        elif msg == win32con.WM_COMMAND:
            cmd = win32api.LOWORD(wparam)
            if cmd == 1:
                self.restore()
            elif cmd == 2:
                self.quit_app()
            elif cmd == 3:
                # 托盘右键 → 清理缓存
                try: self.app.on_clean_cache()
                except Exception: pass
        elif msg == win32con.WM_DESTROY:
            self.destroy()
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _show_menu(self):
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, 1, "显示主窗口")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 3, "清理缓存释放覆盖层")
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, 2, "退出")
        pos = win32api.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN,
                                pos[0], pos[1], 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)

    def update_tooltip(self, text):
        if self.visible and self.hwnd:
            try:
                nid = (self.hwnd, 0, win32gui.NIF_TIP, self.WM_TRAY,
                       self.hicon, "UWF Manager Pro - " + str(text))
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
            except Exception:
                pass

    def update_icon(self, text, warn=False):
        """动态更新托盘图标（显示剩余内存数字）。text 如 '3.2G' / '45%' / '1.2G'。
        warn=True 时文字变红色警示。相同文本跳过渲染以节省 CPU。"""
        if text == self._last_text:
            return
        self._last_text = text
        try:
            new_hicon = self._create_hicon_from_text(text, warn=warn)
            if new_hicon and self.hicon != new_hicon:
                # 销毁旧图标句柄
                if self.hicon:
                    try: win32gui.DestroyIcon(self.hicon)
                    except Exception: pass
                self.hicon = new_hicon
                if self.visible and self.hwnd:
                    nid = (self.hwnd, 0, win32gui.NIF_ICON,
                           self.WM_TRAY, self.hicon, "")
                    win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        except Exception:
            pass

    def _create_hicon_from_text(self, text, warn=False):
        """用纯 GDI 在位图上绘制数字文字生成 HICON（不依赖 PIL）。
        方案：CreateCompatibleBitmap → FillRect(背景) → CreateFontW+DrawTextW(文字) → CreateIconIndirect"""
        import ctypes
        from ctypes import wintypes

        size = self.ICON_SIZE  # 64x64
        bg = self.ICON_BG       # (45,45,45) 深灰
        fg = self.ICON_FG_WARN if warn else self.ICON_FG_NORMAL  # 金黄 or 红

        hdc_screen = ctypes.windll.user32.GetDC(0)

        # --- 1. 创建内存 DC 和 32-bit DIB 位图 ---
        class BITMAPV5HEADER(ctypes.Structure):
            _fields_ = [
                ("bV5Size", wintypes.DWORD), ("bV5Width", wintypes.LONG),
                ("bV5Height", wintypes.LONG), ("bV5Planes", wintypes.WORD),
                ("bV5BitCount", wintypes.WORD), ("bV5Compression", wintypes.DWORD),
                ("bV5SizeImage", wintypes.DWORD), ("bV5XPelsPerMeter", wintypes.LONG),
                ("bV5YPelsPerMeter", wintypes.LONG), ("bV5ClrUsed", wintypes.DWORD),
                ("bV5ClrImportant", wintypes.DWORD),
                ("bV5RedMask", wintypes.DWORD), ("bV5GreenMask", wintypes.DWORD),
                ("bV5BlueMask", wintypes.DWORD), ("bV5AlphaMask", wintypes.DWORD),
                ("bV5CSType", wintypes.DWORD),
                ("bV5Endpoints", ctypes.c_byte * 36),
                ("bV5GammaRed", wintypes.DWORD), ("bV5GammaGreen", wintypes.DWORD),
                ("bV5GammaBlue", wintypes.DWORD),
                ("bV5Intent", wintypes.DWORD), ("bV5ProfileData", wintypes.DWORD),
                ("bV5ProfileSize", wintypes.DWORD), ("bV5Reserved", wintypes.DWORD),
            ]

        hdr = BITMAPV5HEADER()
        hdr.bV5Size = ctypes.sizeof(BITMAPV5HEADER)
        hdr.bV5Width = size
        hdr.bV5Height = -size          # top-down 负值
        hdr.bV5Planes = 1
        hdr.bV5BitCount = 32           # 32-bit BGRA
        hdr.bV5Compression = 3         # BI_BITFIELDS (BGRA)
        hdr.bV5AlphaMask = 0x00000000  # 不用 alpha（全不透明）
        hdr.bV5RedMask   = 0x00FF0000
        hdr.bV5GreenMask = 0x0000FF00
        hdr.bV5BlueMask  = 0x000000FF
        hdr.bV5SizeImage = size * size * 4

        ppbits = ctypes.c_void_p()
        hbmp_color = ctypes.windll.gdi32.CreateDIBSection(
            hdc_screen, ctypes.byref(hdr), 0, ctypes.byref(ppbits), None, 0)
        if not hbmp_color or not ppbits:
            ctypes.windll.user32.ReleaseDC(0, hdc_screen)
            return None

        # --- 2. 用 GDI 绘制背景和文字 ---
        hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_screen)
        ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp_color)

        # 填充背景色（深灰）
        brush = ctypes.windll.gdi32.CreateSolidBrush(
            (bg[2] << 16) | (bg[1] << 8) | bg[0])  # RGB → COLORREF (BBGGRR)
        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        rect = RECT(0, 0, size, size)
        ctypes.windll.user32.FillRect(hdc_mem, ctypes.byref(rect), brush)
        ctypes.windll.gdi32.DeleteObject(brush)

        # 创建字体（Arial Bold，大小自适应）
        font_size = int(size * 0.55)  # 64*0.55 ≈ 35pt
        hfont = ctypes.windll.gdi32.CreateFontW(
            -font_size, 0, 0, 0, 700,  # height(negative=pt), width, escapement, orientation, weight(bold)
            0, 0, 0, 0,                 # italic, underline, strikeout, charset(ANSI=0)
            3, 2, 1,                    # output precision, clip precision, quality(ANTIALIASED=3/CLEARTYPE=5)
            "Arial")                    # face name
        ctypes.windll.gdi32.SelectObject(hdc_mem, hfont)

        # 设置文字颜色（金黄/红）
        ctypes.windll.gdi32.SetTextColor(
            hdc_mem, (fg[2] << 16) | (fg[1] << 8) | fg[0])
        ctypes.windll.gdi32.SetBkMode(hdc_mem, 1)  # TRANSPARENT

        # 居中绘制文字
        class SIZE(ctypes.Structure):
            _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]
        sz = SIZE()
        ctypes.windll.gdi32.GetTextExtentPoint32W(
            hdc_mem, text, len(text), ctypes.byref(sz))
        x = max(0, (size - sz.cx) // 2)
        y = max(0, (size - sz.cy) // 2) - 2
        ctypes.windll.gdi32.TextOutW(hdc_mem, x, y, text, len(text))

        # 清理 DC 和字体
        ctypes.windll.gdi32.SelectObject(hdc_mem,
            ctypes.windll.gdi32.GetCurrentObject(hdc_mem, 6))  # OBJ_FONT=6
        ctypes.windll.gdi32.DeleteObject(hfont)
        ctypes.windll.gdi32.DeleteDC(hdc_mem)

        # --- 3. AND 掩码：全 0 = 所有像素都不透明 ---
        mask_row_stride = (size + 31) // 32 * 4
        mask_bytes = b'\x00' * (mask_row_stride * size)
        hbmp_mask = ctypes.windll.gdi32.CreateBitmap(
            size, size, 1, 1, mask_bytes)

        # --- 4. 创建图标 ---
        class ICONINFO(ctypes.Structure):
            _fields_ = [
                ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmColor", wintypes.HBITMAP),
                ("hbmMask", wintypes.HBITMAP)]

        ii = ICONINFO(True, size // 2, size // 2, hbmp_color, hbmp_mask)
        hicon = ctypes.windll.user32.CreateIconIndirect(ctypes.byref(ii))

        # 清理 GDI 对象
        ctypes.windll.gdi32.DeleteObject(hbmp_color)
        ctypes.windll.gdi32.DeleteObject(hbmp_mask)
        ctypes.windll.user32.ReleaseDC(0, hdc_screen)
        return hicon

    def show_balloon(self, title, message, warn=False):
        """显示托盘气泡通知（Windows 10+ 支持）。"""
        if not self.visible or not self.hwnd:
            return
        try:
            # NIIF_WARNING=3(黄), NIIF_ERROR=2(红), NIIF_INFO=1(蓝/默认)
            icon_flag = 2 if warn else 1  # 红色警示 / 蓝色信息
            # Shell_NotifyIcon 需要特殊结构，用 NIM_MODIFY + NIF_INFO
            import ctypes
            class NOTIFYICONDATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("hWnd", ctypes.c_void_p),
                    ("uID", ctypes.c_uint),
                    ("uFlags", ctypes.c_uint),
                    ("uCallbackMessage", ctypes.c_uint),
                    ("hIcon", ctypes.c_void_p),
                    ("szTip", ctypes.c_wchar * 128),
                    ("dwState", ctypes.c_uint),
                    ("dwStateMask", ctypes.c_uint),
                    ("szInfo", ctypes.c_wchar * 256),
                    ("uTimeoutOrVersion", ctypes.c_union),
                    ("szInfoTitle", ctypes.c_wchar * 64),
                    ("dwInfoFlags", ctypes.c_uint),
                ]
            nid_data = NOTIFYICONDATA()
            nid_data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid_data.hWnd = self.hwnd
            nid_data.uID = 0
            nid_data.uFlags = win32gui.NIF_INFO | win32gui.NIF_ICON
            nid_data.hIcon = self.hicon if self.hicon else 0
            nid_data.szInfo = message[:255]
            nid_data.szInfoTitle = title[:63]
            nid_data.dwInfoFlags = icon_flag
            nid_data.uTimeoutOrVersion = 10000
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid_data)
        except Exception:
            pass

    def restore(self):
        try:
            self.app.root.deiconify()
            self.app.root.lift()
            self.app.root.focus_force()
        except Exception:
            pass

    def hide_main(self):
        try:
            self.app.root.withdraw()
        except Exception:
            pass

    def quit_app(self):
        try:
            self.destroy()
        except Exception:
            pass
        try:
            self.app.root.destroy()
        except Exception:
            pass

    def destroy(self):
        if self.visible and self.hwnd:
            try:
                win32gui.Shell_NotifyIcon(
                    win32gui.NIM_DELETE, (self.hwnd, 0))
            except Exception:
                pass
        self.visible = False


# ==================== 主应用类 ====================
class UWFApp:
    def __init__(self, root):
        self.root = root
        self.admin = self._check_admin()
        self.status_gen = 0
        self.log_gen = 0
        self.settings_gen = 0
        self.rt_gen = 0
        self.reg_gen = 0
        self._rendered = False
        self._pending_protect = {}   # 盘符 -> True(待生效保护)/False(待取消)
        # 实时写入监控状态
        self.monitor = overlay_monitor.OverlayMonitor()
        self.monitoring = False
        self.log_map = {}          # path -> treeview row id（去重）
        self.log_records = []      # (ts, path, action, size_bytes) 用于导出
        self.log_event_count = 0   # 本次会话捕获的写入事件总数
        self.MAX_LOG_ROWS = 4000   # 列表最多显示的不同文件数
        self.EXPORT_CAP = 20000    # 导出记录上限
        self.tray = SystemTrayIcon(self)
        self._setup_ui()
        self.refresh()
        self._start_auto_refresh()

    # ---------- 管理员检测 ----------
    def _check_admin(self):
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # ---------- COM 线程执行器 ----------
    def _run_com(self, gen_attr, fn, on_success, on_fail):
        my_gen = getattr(self, gen_attr) + 1
        setattr(self, gen_attr, my_gen)

        def work():
            pythoncom.CoInitialize()
            try:
                result = fn()
                cur = getattr(self, gen_attr)
                if my_gen == cur:
                    self.root.after(0, lambda: on_success(result))
            except uwf_core.UWFNotSupported as e:
                cur = getattr(self, gen_attr)
                if my_gen == cur:
                    self.root.after(0, lambda: on_fail(str(e), False))
            except Exception as e:
                cur = getattr(self, gen_attr)
                if my_gen == cur:
                    self.root.after(0, lambda: on_fail(str(e), True))
            finally:
                pythoncom.CoUninitialize()

        threading.Thread(target=work, daemon=True).start()

    # ==================== UI 布局 ====================
    def _setup_ui(self):
        self.root.title("UWF Manager Pro v2.10")
        self.root.geometry("1100x800")
        self.root.configure(bg=BG)
        self.root.minsize(900, 680)
        # 关闭窗口时最小化到托盘
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Accent.Horizontal.TProgressbar",
                             background=ACCENT, troughcolor="#E0E0E0",
                             borderwidth=0, thickness=18)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # 标题栏
        title_bar = tk.Frame(self.root, bg=ACCENT, height=48)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="UWF Manager Pro v2.10",
                 font=FONT_TITLE, fg="white", bg=ACCENT).pack(
            side=tk.LEFT, padx=18, pady=8)
        self.lbl_admin = tk.Label(title_bar, text="", font=FONT_BOLD,
                                  fg="#FFD700", bg=ACCENT)
        self.lbl_admin.pack(side=tk.RIGHT, padx=18)

        # Notebook (选项卡式布局)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        # ---- Tab 1: 状态概览 ----
        tab1 = ttk.Frame(self.notebook)
        self.notebook.add(tab1, text="  状态概览  ")
        self._build_tab_status(tab1)

        # ---- Tab 2: 设置面板 ----
        tab2 = ttk.Frame(self.notebook)
        self.notebook.add(tab2, text="  设置  ")
        self._build_tab_settings(tab2)

        # ---- Tab 3: 排除列表 ----
        tab3 = ttk.Frame(self.notebook)
        self.notebook.add(tab3, text="  排除列表  ")
        self._build_tab_exclusions(tab3)

        # ---- Tab 4: 文件分析 ----
        tab4 = ttk.Frame(self.notebook)
        self.notebook.add(tab4, text="  文件分析  ")
        self._build_tab_files(tab4)

        # ---- Tab 5: 注册表排除 ----
        tab5 = ttk.Frame(self.notebook)
        self.notebook.add(tab5, text="  注册表排除  ")
        self._build_tab_registry(tab5)

        # 底部信息栏
        self.lbl_msg = tk.Label(self.root, text="", font=FONT,
                                fg=TEXT_SUB, bg=BG)
        self.lbl_msg.pack(anchor="w", padx=12, pady=(0, 6))

    # ==================== Tab 1: 状态概览 ====================
    def _build_tab_status(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.canvas1 = canvas
        self._bind_mousewheel(canvas)

        # ====== UWF 引导开启卡片（未安装时显示）======
        self.guide_frame = tk.Frame(content, bg=BG)
        self.guide_frame.pack(fill=tk.X, pady=(0, 8))

        guide_inner = tk.Frame(self.guide_frame, bg="#FFF4CE",  # 浅黄背景（警告/引导风格）
                               highlightbackground="#E6B800", highlightthickness=1)
        guide_inner.pack(fill=tk.X, padx=2, pady=2)

        guide_title_row = tk.Frame(guide_inner, bg="#FFF4CE")
        guide_title_row.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(guide_title_row, text="💡 UWF 未启用  |  UWF Not Enabled",
                 font=("Segoe UI", 11, "bold"), fg="#7A5C00", bg="#FFF4CE").pack(side=tk.LEFT)

        guide_desc = tk.Label(guide_inner,
            text="统一写入筛选器（UWF）是 Windows 内置的「影子系统」功能。\n"
                 "启用后每次重启自动还原，适合公用电脑、自助终端、亲子保护等场景。\n\n"
                 "Unified Write Filter (UWF) is Windows' built-in \"shadow system\" feature.\n"
                 "After enabled, the system auto-restores on every reboot. "
                 "Perfect for public PCs, kiosks, child protection, etc.",
            font=FONT, fg="#5A4A00", bg="#FFF4CE", justify=tk.LEFT)
        guide_desc.pack(anchor="w", padx=12, pady=(0, 8))

        guide_btn_row = tk.Frame(guide_inner, bg="#FFF4CE")
        guide_btn_row.pack(fill=tk.X, padx=12, pady=(0, 10))

        self.btn_enable_uwf = ttk.Button(guide_btn_row, text="🔧 一键开启 UWF (自动)  |  Enable UWF Automatically",
                                         command=self.on_enable_uwf_auto, width=36)
        self.btn_enable_uwf.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(guide_btn_row, text="📂 打开 Windows 功能面板  |  Open Windows Features",
                   command=self.on_open_windows_features, width=34).pack(side=tk.LEFT)

        self.guide_status = tk.Label(guide_inner, text="", font=("Segoe UI", 8),
                                      fg="#7A5C00", bg="#FFF4CE")
        self.guide_status.pack(anchor="w", padx=12, pady=(0, 6))

        # --- UWF 状态卡片 ---
        inner = self._card(content, "UWF 状态")
        row = tk.Frame(inner, bg=CARD_BG)
        row.pack(fill=tk.X)
        self.lbl_status = tk.Label(row, text="检测中…", font=FONT_BIG,
                                   fg=TEXT_SUB, bg=CARD_BG)
        self.lbl_status.pack(side=tk.LEFT)
        self.lbl_mode = tk.Label(row, text="", font=FONT, fg=TEXT_SUB,
                                 bg=CARD_BG)
        self.lbl_mode.pack(side=tk.LEFT, padx=20)

        btn_row = tk.Frame(inner, bg=CARD_BG)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self.btn_toggle = ttk.Button(btn_row, text="开启保护",
                                     command=self.on_toggle, width=14)
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="提交所有删除", width=14,
                   command=self.on_commit_all).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="重启系统", width=12,
                   command=self.on_restart).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="关机保护", width=12,
                   command=self.on_shutdown).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_row, text="刷新", width=8,
                   command=self.refresh).pack(side=tk.LEFT, padx=3)

        # --- 覆盖层内存使用卡片 ---
        inner = self._card(content, "覆盖层内存使用")
        self.lbl_overlay = tk.Label(inner, text="—", font=FONT_BIG,
                                    fg=ACCENT, bg=CARD_BG)
        self.lbl_overlay.pack(anchor="w")
        self.bar_overlay = ttk.Progressbar(inner, length=500,
                                           mode="determinate")
        self.bar_overlay.pack(fill=tk.X, pady=(6, 4))
        self.lbl_overlay_detail = tk.Label(inner, text="", font=FONT,
                                           fg=TEXT_SUB, bg=CARD_BG)
        self.lbl_overlay_detail.pack(anchor="w")

        # --- 清理缓存按钮（覆盖层卡片内）---
        cache_btn_row = tk.Frame(inner, bg=CARD_BG)
        cache_btn_row.pack(fill=tk.X, pady=(6, 0))
        self.btn_clean_cache = ttk.Button(cache_btn_row, text="🧹 清理缓存释放覆盖层",
                                          command=self.on_clean_cache, width=24)
        self.btn_clean_cache.pack(side=tk.LEFT)
        self.lbl_cache_hint = tk.Label(cache_btn_row, text="清理临时文件/浏览器缓存等释放 UWF 空间",
                                       font=("Segoe UI", 8), fg=TEXT_SUB, bg=CARD_BG)
        self.lbl_cache_hint.pack(side=tk.LEFT, padx=8)

        # --- 受保护卷 ---
        inner = self._card(content, "受保护卷")
        cols = ("盘符", "保护状态", "覆盖占用", "重启后", "提交待处理")
        self.tree_vol = ttk.Treeview(inner, columns=cols, show="headings",
                                     height=4)
        for c in cols:
            self.tree_vol.heading(c, text=c)
        self.tree_vol.column("盘符", width=55)
        self.tree_vol.column("保护状态", width=90)
        self.tree_vol.column("覆盖占用", width=120)
        self.tree_vol.column("重启后", width=70)
        self.tree_vol.column("提交待处理", width=85)
        self.tree_vol.pack(fill=tk.X, pady=4)

        vol_btns = tk.Frame(inner, bg=CARD_BG)
        vol_btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(vol_btns, text="保护 C:", width=10,
                   command=lambda: self.on_protect_volume("C:")).pack(
            side=tk.LEFT, padx=2)
        ttk.Button(vol_btns, text="不保护 C:", width=10,
                   command=lambda: self.on_unprotect_volume("C:")).pack(
            side=tk.LEFT, padx=2)

        # --- 覆盖层写入监控（实时） ---
        inner = self._card(content, "覆盖层写入监控（实时）")
        tk.Label(inner,
                 text="开启后实时捕获写入 C: 覆盖层的文件（新增/修改）。"
                      "UWF 启用时所有写入都进覆盖层，故监控真实文件系统即等价。",
                 font=FONT, fg=TEXT_SUB, bg=CARD_BG).pack(anchor="w")
        opt = tk.Frame(inner, bg=CARD_BG)
        opt.pack(fill=tk.X, pady=(6, 2))
        self.btn_log_toggle = ttk.Button(opt, text="开启记录", width=12,
                                         command=self.on_log_toggle)
        self.btn_log_toggle.pack(side=tk.LEFT, padx=2)
        ttk.Button(opt, text="清空", width=8,
                   command=self.on_clear_log).pack(side=tk.LEFT, padx=2)
        ttk.Button(opt, text="导出 TXT", width=10,
                   command=self.on_export_log).pack(side=tk.LEFT, padx=2)
        self.lbl_log = tk.Label(opt, text="未开启", bg=CARD_BG, fg=TEXT_SUB,
                                font=FONT)
        self.lbl_log.pack(side=tk.LEFT, padx=8)

        log_cols = ("时间", "原始路径", "大小", "操作")
        self.tree_log = ttk.Treeview(inner, columns=log_cols,
                                     show="headings", height=10)
        for c in log_cols:
            self.tree_log.heading(c, text=c)
        self.tree_log.column("时间", width=80)
        self.tree_log.column("原始路径", width=470)
        self.tree_log.column("大小", width=85)
        self.tree_log.column("操作", width=70)
        self.tree_log.pack(fill=tk.X, pady=4)
        self.tree_log.bind("<Double-1>", self.on_open_log_file)
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label="打开位置", command=self.on_open_log_menu)
        self.log_menu.add_command(label="复制路径", command=self.on_copy_log_path)
        self.log_menu.add_command(label="提交删除(穿透覆盖)",
                                  command=self.on_commit_log_delete)
        self.tree_log.bind("<Button-3>", self.on_log_rightclick)

        self.lbl_log_summary = tk.Label(inner, text="尚未开始记录",
                                        font=FONT_BOLD, fg=ACCENT, bg=CARD_BG)
        self.lbl_log_summary.pack(anchor="w", pady=(4, 0))

    # ==================== Tab 2: 设置面板 ====================
    def _build_tab_settings(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._bind_mousewheel(canvas)

        # ===== 左侧：基本设置 + 缓存设置 =====
        left_col = tk.Frame(content, bg=BG)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 4), pady=8)

        # --- 基本设置 ---
        inner = self._card(left_col, "基本设置")
        grid = tk.Frame(inner, bg=CARD_BG)
        grid.pack(fill=tk.X)

        # 写入过滤
        r = 0
        tk.Label(grid, text="写入过滤:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.var_filter = tk.StringVar(value="—")
        self.cb_filter = ttk.Combobox(grid, textvariable=self.var_filter,
                                      values=["启用", "禁用"], state="readonly",
                                      width=14)
        self.cb_filter.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        # 覆盖类型
        tk.Label(grid, text="覆盖类型:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.var_ovl_type = tk.StringVar(value="—")
        self.cb_type = ttk.Combobox(grid, textvariable=self.var_ovl_type,
                                    values=["基于内存", "基于磁盘"],
                                    state="readonly", width=14)
        self.cb_type.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        # HORM
        tk.Label(grid, text="HORM:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.var_horm = tk.StringVar(value="—")
        self.cb_horm = ttk.Combobox(grid, textvariable=self.var_horm,
                                    values=["启用", "禁用"], state="readonly",
                                    width=14)
        self.cb_horm.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        # 服务模式
        tk.Label(grid, text="服务模式:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.var_servicing = tk.StringVar(value="—")
        self.cb_servicing = ttk.Combobox(grid, textvariable=self.var_servicing,
                                         values=["启用", "禁用"], state="readonly",
                                         width=14)
        self.cb_servicing.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        btn_row = tk.Frame(inner, bg=CARD_BG)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self.btn_apply_basic = ttk.Button(btn_row, text="应用基本设置", width=16,
                                          command=self.on_apply_basic)
        self.btn_apply_basic.pack(side=tk.LEFT, padx=2)
        self.btn_apply_servicing = ttk.Button(btn_row, text="应用服务模式", width=16,
                                              command=self.on_apply_servicing)
        self.btn_apply_servicing.pack(side=tk.LEFT, padx=2)

        # --- 缓存设置(MB) ---
        inner = self._card(left_col, "缓存设置 (MB)")
        grid = tk.Frame(inner, bg=CARD_BG)
        grid.pack(fill=tk.X)

        r = 0
        tk.Label(grid, text="最大缓存:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.ent_max_size = ttk.Entry(grid, width=14)
        self.ent_max_size.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        tk.Label(grid, text="警告阈值:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.ent_warn = ttk.Entry(grid, width=14)
        self.ent_warn.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        tk.Label(grid, text="严重阈值:", bg=CARD_BG, font=FONT).grid(
            row=r, column=0, sticky="e", padx=4, pady=5)
        self.ent_crit = ttk.Entry(grid, width=14)
        self.ent_crit.grid(row=r, column=1, sticky="w", padx=4, pady=5)
        r += 1

        # 可用空间（只读显示）
        self.lbl_avail_space = tk.Label(grid, text="", font=FONT,
                                        fg=ACCENT, bg=CARD_BG)
        self.lbl_avail_space.grid(row=r, column=1, sticky="w", padx=4, pady=5)

        btn_row = tk.Frame(inner, bg=CARD_BG)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self.btn_apply_cache = ttk.Button(btn_row, text="确认缓存", width=14,
                                          command=self.on_apply_cache)
        self.btn_apply_cache.pack(side=tk.LEFT, padx=2)

        # ===== 右侧：分区保护设置 =====
        right_col = tk.Frame(content, bg=BG)
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        inner = self._card(right_col, "分区保护设置（当前状态 / 重启后状态）")
        cols = ("分区", "当前状态", "重启后状态")
        self.tree_protect = ttk.Treeview(inner, columns=cols, show="headings",
                                         height=6)
        for c in cols:
            self.tree_protect.heading(c, text=c)
        self.tree_protect.column("分区", width=60)
        self.tree_protect.column("当前状态", width=100)
        self.tree_protect.column("重启后状态", width=100)
        self.tree_protect.pack(fill=tk.X, pady=4)
        # 右键菜单
        self.prot_menu = tk.Menu(self.root, tearoff=0)
        self.prot_menu.add_command(label="设为已保护",
                                   command=self.on_protect_selected)
        self.prot_menu.add_command(label="设为不保护",
                                   command=self.on_unprotect_selected)
        self.tree_protect.bind("<Button-3>", self.on_prot_rightclick)

        prot_btns = tk.Frame(inner, bg=CARD_BG)
        prot_btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(prot_btns, text="保护选中", width=12,
                   command=self.on_protect_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(prot_btns, text="不保护选中", width=12,
                   command=self.on_unprotect_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(prot_btns, text="立即重启生效", width=14,
                   command=self.on_restart).pack(side=tk.LEFT, padx=2)

    # ==================== Tab 3: 排除列表 ====================
    def _build_tab_exclusions(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 上半部分：添加排除
        add_frame = tk.Frame(top, bg=BG)
        add_frame.pack(fill=tk.X, pady=(0, 8))

        inner = self._card(add_frame, "添加排除项")
        grid = tk.Frame(inner, bg=CARD_BG)
        grid.pack(fill=tk.X)

        tk.Label(grid, text="目标分区:", bg=CARD_BG, font=FONT).grid(
            row=0, column=0, sticky="e", padx=4, pady=5)
        self.var_exc_drive = tk.StringVar(value="C:")
        cb_d = ttk.Combobox(grid, textvariable=self.var_exc_drive,
                             values=["C:", "D:", "E:"], state="readonly",
                             width=8)
        cb_d.grid(row=0, column=1, sticky="w", padx=4, pady=5)

        tk.Label(grid, text="路径:", bg=CARD_BG, font=FONT).grid(
            row=0, column=2, sticky="e", padx=(12, 4), pady=5)
        self.ent_exc_path = ttk.Entry(grid, width=50)
        self.ent_exc_path.grid(row=0, column=3, sticky="we", padx=4, pady=5)
        grid.columnconfigure(3, weight=1)

        tk.Label(grid, text="(相对卷根路径, 如 \\Users\\YourName\\.codex)",
                 bg=CARD_BG, font=("Segoe UI", 8), fg=TEXT_SUB).grid(
            row=1, column=3, sticky="w", padx=4)

        exc_btns = tk.Frame(inner, bg=CARD_BG)
        exc_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(exc_btns, text="文件选择…", width=12,
                   command=self.on_exc_browse_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(exc_btns, text="文件夹选择…", width=14,
                   command=self.on_exc_browse_dir).pack(side=tk.LEFT, padx=2)
        ttk.Button(exc_btns, text="添加排除", width=12,
                   command=self.on_add_exclusion).pack(side=tk.LEFT, padx=2)
        ttk.Button(exc_btns, text="清除全部", width=12,
                   command=self.on_clear_exclusions).pack(side=tk.LEFT, padx=2)

        # 下半部分：排除列表
        inner = self._card(top, f"写入过滤排除列表（鼠标右键可删除）")
        exc_cols = ("分区", "排除路径")
        self.tree_exc = ttk.Treeview(inner, columns=exc_cols,
                                     show="headings", height=15)
        for c in exc_cols:
            self.tree_exc.heading(c, text=c)
        self.tree_exc.column("分区", width=55)
        self.tree_exc.column("排除路径", width=700)
        self.tree_exc.pack(fill=tk.BOTH, expand=True, pady=4)
        self.exc_menu = tk.Menu(self.root, tearoff=0)
        self.exc_menu.add_command(label="删除选中", command=self.on_remove_exc_sel)
        self.exc_menu.add_command(label="复制路径", command=self.on_copy_exc_path)
        self.tree_exc.bind("<Button-3>", self.on_exc_rightclick)

        exc_bot = tk.Frame(inner, bg=CARD_BG)
        exc_bot.pack(fill=tk.X, pady=(4, 0))
        self.lbl_exc_count = tk.Label(exc_bot, text="", bg=CARD_BG, fg=TEXT_SUB,
                                      font=FONT)
        self.lbl_exc_count.pack(side=tk.LEFT)
        ttk.Button(exc_bot, text="导入列表", width=12,
                   command=self.on_import_exc).pack(side=tk.RIGHT, padx=2)
        ttk.Button(exc_bot, text="导出列表", width=12,
                   command=self.on_export_exc).pack(side=tk.RIGHT, padx=2)
        ttk.Button(exc_bot, text="刷新列表", width=12,
                   command=self.on_refresh_exc).pack(side=tk.RIGHT, padx=2)

    # ==================== Tab 4: 文件分析 ====================
    def _build_tab_files(self, parent):
        canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._bind_mousewheel(canvas)

        # --- 文件浏览器 ---
        inner = self._card(content, "文件浏览器（覆盖层占用来源分析）")
        opt = tk.Frame(inner, bg=CARD_BG)
        opt.pack(fill=tk.X, pady=(6, 2))
        tk.Label(opt, text="天数:", bg=CARD_BG, font=FONT).pack(side=tk.LEFT)
        self.var_days = tk.StringVar(value="30")
        ttk.Combobox(opt, textvariable=self.var_days, width=6,
                     values=("7", "14", "30", "90")).pack(side=tk.LEFT, padx=4)
        tk.Label(opt, text="最小 MB:", bg=CARD_BG, font=FONT).pack(
            side=tk.LEFT, padx=(8, 0))
        self.var_min = tk.StringVar(value="10")
        ttk.Entry(opt, textvariable=self.var_min, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Button(opt, text="扫描", width=10,
                   command=self.on_scan).pack(side=tk.LEFT, padx=8)
        self.lbl_scan = tk.Label(opt, text="", bg=CARD_BG, fg=TEXT_SUB, font=FONT)
        self.lbl_scan.pack(side=tk.LEFT, padx=8)

        file_cols = ("路径", "大小", "修改时间", "类型")
        self.tree_file = ttk.Treeview(inner, columns=file_cols,
                                      show="headings", height=12)
        for c in file_cols:
            self.tree_file.heading(c, text=c)
        self.tree_file.column("路径", width=520)
        self.tree_file.column("大小", width=90)
        self.tree_file.column("修改时间", width=140)
        self.tree_file.column("类型", width=60)
        self.tree_file.pack(fill=tk.X, pady=4)
        self.tree_file.bind("<Double-1>", self.on_open_file)
        self.file_menu = tk.Menu(self.root, tearoff=0)
        self.file_menu.add_command(label="打开位置", command=self.on_open_file_menu)
        self.file_menu.add_command(label="复制路径", command=self.on_copy_path)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="提交删除(穿透覆盖)",
                                   command=self.on_commit_delete)
        self.tree_file.bind("<Button-3>", self.on_file_rightclick)

        self.lbl_summary = tk.Label(inner, text="", font=FONT, fg=ACCENT,
                                    bg=CARD_BG, justify=tk.LEFT, wraplength=950)
        self.lbl_summary.pack(anchor="w", pady=(4, 0))

        # --- 覆盖层文件（只读）---
        inner = self._card(content, "覆盖层文件（当前会话 · 只读）")
        ovf_top = tk.Frame(inner, bg=CARD_BG)
        ovf_top.pack(fill=tk.X, pady=(4, 2))
        tk.Label(ovf_top, text="分区:", bg=CARD_BG, font=FONT).pack(side=tk.LEFT)
        self.var_ovf_drive = tk.StringVar(value="C:")
        ttk.Combobox(ovf_top, textvariable=self.var_ovf_drive,
                     values=["C:"], width=8, state="readonly").pack(
                         side=tk.LEFT, padx=4)
        ttk.Button(ovf_top, text="刷新列表", width=12,
                   command=self.on_refresh_overlay_files).pack(side=tk.LEFT, padx=6)
        self.lbl_ovf_count = tk.Label(ovf_top, text="", bg=CARD_BG, fg=TEXT_SUB,
                                      font=FONT)
        self.lbl_ovf_count.pack(side=tk.LEFT, padx=6)
        ovf_cols = ("文件路径", "大小")
        self.tree_ovf = ttk.Treeview(inner, columns=ovf_cols, show="headings",
                                     height=8)
        for c in ovf_cols:
            self.tree_ovf.heading(c, text=c)
        self.tree_ovf.column("文件路径", width=640)
        self.tree_ovf.column("大小", width=120)
        self.tree_ovf.pack(fill=tk.BOTH, expand=True, pady=4)

    # ==================== 通用辅助 ====================
    def _card(self, parent, title):
        card = tk.Frame(parent, bg=CARD_BG, relief=tk.RAISED, borderwidth=1)
        card.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(card, text=title, font=FONT_BOLD, fg=TEXT_SUB,
                 bg=CARD_BG).pack(anchor="w", padx=12, pady=(8, 4))
        inner = tk.Frame(card, bg=CARD_BG)
        inner.pack(fill=tk.X, padx=12, pady=(0, 10))
        return inner

    def _bind_mousewheel(self, canvas):
        def on_scroll(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_scroll)

    # ==================== 数据刷新 ====================
    def refresh(self):
        self.lbl_status.config(text="检测中…", fg=TEXT_SUB)
        self._rendered = False
        self.root.after(10000, self._watchdog)

        def fetch():
            c = uwf_core.UWFCore()
            c.connect()
            return (c.get_filter(), c.get_volumes(),
                    c.get_overlay(), c.get_overlay_config())

        self._run_com("status_gen", fetch, self._render_status,
                      self._render_error)

    def _watchdog(self):
        if not self._rendered:
            self.lbl_status.config(text="连接超时", fg=RED)
            self.lbl_mode.config(text="")
            self.lbl_msg.config(
                text="10 秒内未收到数据。请确认以管理员运行后点击「刷新」。")

    def _render_status(self, data, is_error=False, msg=""):
        if is_error:
            self._rendered = True
            self.lbl_status.config(text="不可用", fg=RED)
            self.lbl_msg.config(text=f"错误: {msg}")
            self.lbl_admin.config(
                text="管理员" if self.admin else "非管理员!")
            # UWF 不可用 → 显示引导开启卡片
            try:
                self.guide_frame.pack(fill=tk.X, pady=(0, 8))
            except Exception:
                pass
            return

        self._rendered = True
        # UWF 可用 → 隐藏引导卡片
        try:
            self.guide_frame.pack_forget()
        except Exception:
            pass
        flt, vols, overlay, cfg = data

        # --- 状态 ---
        enabled = flt.get("CurrentEnabled")
        if enabled:
            self.lbl_status.config(text="已启用", fg=GREEN)
            self.btn_toggle.config(text="关闭保护")
        else:
            self.lbl_status.config(text="已禁用", fg=RED)
            self.btn_toggle.config(text="开启保护")
        next_en = flt.get("NextEnabled")
        next_s = "启用" if next_en else "禁用" if next_en is not None else "?"
        self.lbl_mode.config(
            text=f"下次启动: {next_s}  |  "
                 f"HORM: {'开' if flt.get('HORMEnabled') else '关'}  |  "
                 f"关机待处理: {'是' if flt.get('ShutdownPending') else '否'}")
        self.lbl_admin.config(
            text="✓ 管理员" if self.admin else "⚠ 非管理员!")

        # --- 覆盖层内存使用（修复数据）---
        if overlay:
            used_mb = overlay.get("OverlayConsumption") or 0   # 已用 MB
            avail_mb = overlay.get("AvailableSpace") or 0      # 可用 MB
            max_cfg = cfg.get("MaximumSize") or 4096          # 配置上限 MB
            # 总容量 = 已用 + 可用（这是真实剩余空间）
            total_mb = used_mb + avail_mb
            # 但如果配置了上限，应以配置上限为分母
            display_total = max(total_mb, max_cfg)
            pct = (used_mb / display_total * 100) if display_total > 0 else 0
            warn = overlay.get("WarningOverlayThreshold") or 0
            crit = overlay.get("CriticalOverlayThreshold") or 0

            self.lbl_overlay.config(
                text=f"{used_mb:.0f} MB / {display_total:.0f} MB "
                     f"(上限 {max_cfg} MB)")
            self.bar_overlay["value"] = min(pct, 100)
            bar_color = ACCENT
            if crit and used_mb >= crit:
                bar_color = RED
            elif warn and used_mb >= warn:
                bar_color = AMBER
            self.style.configure("Accent.Horizontal.TProgressbar",
                                background=bar_color)
            alert = ""
            if crit and used_mb >= crit:
                alert = " ⚠ 已达临界阈值!"
            elif warn and used_mb >= warn:
                alert = " ⚠ 已超警告阈值"
            self.lbl_overlay_detail.config(
                text=f"已用 {pct:.1f}%  |  可用 {avail_mb:.0f} MB{alert}")
            # 同步更新设置面板的可用空间
            if hasattr(self, 'lbl_avail_space'):
                self.lbl_avail_space.config(
                    text=f"可用空间: {avail_mb:.0f} MB")
            # 托盘显示剩余内存
            self._update_tray(flt, overlay)
        elif cfg.get("MaximumSize"):
            self.lbl_overlay.config(text=f"上限 {cfg['MaximumSize']} MB")
            self.lbl_overlay_detail.config(text="（无实时用量数据）")
        else:
            self.lbl_overlay.config(text="—")
            self.lbl_overlay_detail.config(text="")

        # --- 卷列表 ---
        for i in self.tree_vol.get_children():
            self.tree_vol.delete(i)
        seen = set()
        for v in vols:
            dl = v.get("DriveLetter") or "?"
            if dl in seen:
                continue
            seen.add(dl)
            prot = "已保护" if v.get("CurrentProtected") else "未保护"
            cons = v.get("OverlayConsumption")
            cons_s = f"{cons:.0f} MB" if cons else "—"
            nxt = v.get("NextProtected")
            nxt_s = ("已保护" if nxt else "未保护") if nxt is not None else "不变"
            cp = v.get("CommitPending")
            cp_s = "有" if cp else "无"
            self.tree_vol.insert("", "end", values=(
                dl, prot, cons_s, nxt_s, cp_s))

        # --- 更新设置面板 ---
        self._update_settings_panel(flt, cfg, overlay)
        # --- 更新保护表格 ---
        self._update_protect_table(vols)
        # --- 刷新排除列表 ---
        self._refresh_exclusions_ui()
        # --- 刷新注册表排除列表 ---
        self._refresh_registry_ui()

        self.lbl_msg.config(
            text=f"最后刷新: {time.strftime('%H:%M:%S')}  |  "
                 f"root\\standardcimv2\\embedded ✓")

    def _update_tray(self, flt, overlay):
        """托盘图标 + tooltip 同步更新（动态数字图标 + 剩余内存提示）。"""
        try:
            enabled = flt.get("CurrentEnabled")
            ov = overlay or {}
            avail = ov.get("AvailableSpace") or 0
            max_sz = ov.get("MaximumSize") or 0
            used = ov.get("OverlayConsumption") or 0

            # --- 动态图标文字 ---
            if enabled and avail > 0:
                if max_sz > 0:
                    pct = avail / max_sz * 100
                    if avail >= 1024:
                        icon_text = f"{avail / 1024:.1f}G"
                    else:
                        icon_text = f"{avail:.0f}M"
                else:
                    pct = 100
                    icon_text = f"{avail:.0f}M" if avail >= 100 else f"{avail:.0f}"
                warn = (pct < 20)  # 剩余 < 20% 变红
            elif not enabled:
                icon_text = "OFF"
                warn = False
            else:
                icon_text = "--"
                warn = False
            self.tray.update_icon(icon_text, warn=warn)

            # --- Tooltip ---
            txt = (f"UWF {'已启用' if enabled else '已禁用'}  "
                   f"剩余 {avail:.0f} MB（已用 {used:.0f} MB）")
            if max_sz > 0:
                txt += f"  总容量 {max_sz:.0f} MB"
            self.tray.update_tooltip(txt)

            # --- 覆盖层使用率阈值检测 → 缓存清理提醒 ---
            self._check_overlay_threshold(avail, max_sz)
        except Exception:
            pass

    # ==================== 覆盖层阈值监控 + 缓存清理 ====================

    # 阈值配置：使用率 >= 此值时触发提醒（0.0~1.0）
    OVERLAY_WARN_RATIO = 0.65       # 已用 ≥ 65% 提醒
    OVERLAY_CRITICAL_RATIO = 0.85   # 已用 ≥ 85% 紧急
    _last_warn_time = 0             # 上次提醒时间戳（避免频繁弹）
    _WARN_COOLDOWN = 300            # 冷却 5 分钟（秒）

    def _check_overlay_threshold(self, avail_mb, max_mb):
        """检查覆盖层使用率，超阈值时通过托盘气泡通知用户。"""
        if max_mb <= 0 or avail_mb < 0:
            return
        used_ratio = 1.0 - (avail_mb / max_mb)
        now = time.time()

        if used_ratio >= self.OVERLAY_CRITICAL_RATIO and \
           (now - self._last_warn_time > self._WARN_COOLDOWN):
            self._last_warn_time = now
            used_mb = max_mb - avail_mb
            self.tray.show_balloon(
                "UWF 覆盖层紧急",
                f"已用 {used_mb:.0f}MB / {max_mb:.0f}MB ({used_ratio*100:.0f}%)\n"
                "覆盖层即将耗尽！建议立即清理缓存。",
                warn=True)
        elif used_ratio >= self.OVERLAY_WARN_RATIO and \
             (now - self._last_warn_time > self._WARN_COOLDOWN):
            self._last_warn_time = now
            used_mb = max_mb - avail_mb
            self.tray.show_balloon(
                "UWF 覆盖层偏高",
                f"已用 {used_mb:.0f}MB / {max_mb:.0f}MB ({used_ratio*100:.0f}%)\n"
                "可右键托盘图标选择「清理缓存释放覆盖层」。",
                warn=False)

    def on_enable_uwf_auto(self):
        """一键自动启用 UWF 功能（通过 DISM 命令）。"""
        import subprocess

        self.guide_status.config(text="⏳ 正在启用 UWF... / Enabling UWF, please wait...")
        self.root.update()

        try:
            # 使用 DISM 启用 UWF 功能（需要管理员权限，/norestart 不自动重启）
            result = subprocess.run(
                ["dism", "/online", "/enable-feature",
                 "/featurename:Client-UnifiedWriteFilter",
                 "/all", "/norestart"],
                capture_output=True, text=True, timeout=120,
                encoding="gbk", errors="replace")
            output = result.stdout + result.stderr

            if result.returncode == 0:
                self.guide_status.config(
                    text="✅ UWF 已启用！请重启电脑以完成安装。\n"
                         "✅ UWF enabled! Please restart PC to complete installation.",
                    fg="#107C10")
                messagebox.showinfo(
                    "UWF 已启用 / UWF Enabled",
                    "统一写入筛选器（UWF）功能已成功启用！\n\n"
                    "Unified Write Filter has been enabled successfully!\n\n"
                    "请重启电脑以完成安装。 / Please restart your PC.\n\n"
                    "重启后打开本软件即可开始使用 UWF。\n"
                    "After reboot, open this app to start using UWF.")
            else:
                # 检查是否是"已启用"的错误（返回码可能非 0 但实际成功）
                if "已启用" in output or "enabled" in output.lower():
                    self.guide_status.config(text="✅ UWF 已经是启用状态。请重启确认。", fg="#107C10")
                    messagebox.showinfo("提示", "UWF 已经是启用状态。请重启电脑确认。")
                else:
                    self.guide_status.config(text=f"❌ 启用失败 (code {result.returncode})", fg="#D13438")
                    messagebox.showerror(
                        "启用失败 / Enable Failed",
                        f"DISM 返回码: {result.returncode}\n\n{output[:500]}")
        except subprocess.TimeoutExpired:
            self.guide_status.config(text="❌ 操作超时（>120秒）", fg="#D13438")
            messagebox.showerror("超时", "DISM 操作超时。请手动启用或检查网络。")
        except FileNotFoundError:
            self.guide_status.config(text="❌ 找不到 DISM 工具", fg="#D13438")
            messagebox.showerror("错误", "找不到 DISM 工具。请使用「打开 Windows 功能面板」手动启用。")
        except Exception as ex:
            self.guide_status.config(text=f"❌ 错误: {ex}", fg="#D13438")
            messagebox.showerror("错误", f"启用 UWF 时出错：\n{ex}")

    def on_open_windows_features(self):
        """打开 Windows 可选功能控制面板页面（让用户手动勾选 UWF）。"""
        import subprocess
        try:
            # 打开「启用或关闭 Windows 功能」控制面板
            subprocess.Popen("optionalfeatures", shell=True)
            self.guide_status.config(
                text="📂 已打开 Windows 功能面板 → 设备锁定 → 勾选「统一写入筛选器」→ 确定",
                fg="#7A5C00")
        except Exception as ex:
            # 备用方案：用 control.exe
            try:
                subprocess.Popen(
                    'control.exe "appwiz.cpl,,2"', shell=True)
            except Exception:
                messagebox.showerror(
                    "错误 / Error",
                    f"无法打开 Windows 功能面板。\n"
                    f"Cannot open Windows Features panel.\n\n"
                    f"请手动操作：控制面板 > 程序 > 启用或关闭 Windows 功能 "
                    f"> 设备锁定 > 统一写入筛选器")

    def on_clean_cache(self):
        """执行缓存清理：删除临时文件/浏览器缓存等，释放 UWF 覆盖层空间。"""
        import glob as _glob
        import shutil

        # 定义清理目标：(标签, 路径模式列表, 说明)
        targets = [
            ("用户临时文件", [
                os.path.join(os.environ.get("TEMP", ""), "*"),
            ], "%%TEMP%% 目录"),
            ("系统临时文件", [
                r"C:\Windows\Temp\*",
            ], "C:\\Windows\\Temp"),
            ("Windows 预读取", [
                r"C:\Windows\Prefetch\*.pf",
            ], "Prefetch (*.pf)"),
            ("缩略图缓存", [
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             r"Microsoft\Windows\Explorer\thumbcache_*.db"),
            ], "缩略图缓存"),
            ("Chrome 缓存", [
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             r"Google\Chrome\User Data\Default\Cache\*"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             r"Google\Chrome\User Data\Default\Code Cache\*"),
            ], "Chrome 浏览器缓存"),
            ("Edge 缓存", [
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             r"Microsoft\Edge\User Data\Default\Cache\*"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             r"Microsoft\Edge\User Data\Default\Code Cache\*"),
            ], "Edge 浏览器缓存"),
            ("Windows 更新缓存", [
                r"C:\Windows\SoftwareDistribution\Download\*",
            ], "Windows Update 下载缓存"),
        ]

        # 确认对话框
        msg = ("即将清理以下缓存以释放 UWF 覆盖层空间：\n\n"
               + "\n".join(f"  • {t[2]}" for t in targets)
               + "\n\n这些均为临时/缓存文件，删除安全。\n是否继续？")
        if not messagebox.askyesno("确认清理缓存", msg):
            return

        total_freed = 0
        details = []
        for label, patterns, desc in targets:
            freed = 0
            count = 0
            for pat in patterns:
                for fpath in _glob.glob(pat):
                    try:
                        if os.path.isfile(fpath):
                            sz = os.path.getsize(fpath)
                            os.remove(fpath)
                            freed += sz
                            count += 1
                        elif os.path.isdir(fpath):
                            shutil.rmtree(fpath, ignore_errors=True)
                            count += 1
                    except (OSError, PermissionError, FileNotFoundError):
                        continue
            total_freed += freed
            if freed > 0 or count > 0:
                details.append(f"  {label}: 清理 {count} 项, "
                               f"释放 {human_size(freed)}")

        # 报告结果
        report = (f"清理完成！共释放: **{human_size(total_freed)}**\n\n"
                  + "\n".join(details) if details else "未找到可清理的文件。")
        messagebox.showinfo("缓存清理报告", report)

        # 刷新覆盖层数据（清理后数值应变大）
        self._auto_refresh()

    def _update_settings_panel(self, flt, cfg, overlay=None):
        """将数据填入设置面板控件。"""
        # 记录当前 UWF 启用状态（用于联动锁定）
        self.ufw_enabled = bool(flt.get("CurrentEnabled", False))
        # 写入过滤
        if flt.get("CurrentEnabled") is not None:
            self.var_filter.set("启用" if flt["CurrentEnabled"] else "禁用")
        # 覆盖类型
        ovl_type = cfg.get("Type")
        if ovl_type is not None:
            self.var_ovl_type.set(OVERLAY_TYPES.get(ovl_type, str(ovl_type)))
        # HORM
        if flt.get("HORMEnabled") is not None:
            self.var_horm.set("启用" if flt["HORMEnabled"] else "禁用")
        # 服务模式
        if hasattr(self, "var_servicing"):
            try:
                sc = uwf_core.UWFCore()
                sc.connect()
                svc = sc.get_servicing()
                if svc:
                    ne = svc.get("NextEnabled")
                    ce = svc.get("CurrentEnabled")
                    val = ne if ne is not None else ce
                    if val is not None:
                        self.var_servicing.set("启用" if val else "禁用")
            except Exception:
                pass
        # 缓存值
        if cfg.get("MaximumSize"):
            self.ent_max_size.delete(0, tk.END)
            self.ent_max_size.insert(0, str(cfg["MaximumSize"]))
        # 阈值（从 UWF_Overlay 读取）
        if overlay:
            warn_val = overlay.get("WarningOverlayThreshold")
            crit_val = overlay.get("CriticalOverlayThreshold")
            if warn_val is not None:
                self.ent_warn.delete(0, tk.END)
                self.ent_warn.insert(0, str(int(warn_val)))
            if crit_val is not None:
                self.ent_crit.delete(0, tk.END)
                self.ent_crit.insert(0, str(int(crit_val)))
        # --- UWF 状态联动：UWF 启用时设置不可编辑（灰色只读）---
        self._apply_settings_lock(self.ufw_enabled)

    def _apply_settings_lock(self, uwf_enabled):
        """UWF 启用时，基本设置与缓存设置全部锁定为灰色只读；
        UWF 禁用时才可编辑（修改需重启生效，故非 UWF 模式下才允许保存）。"""
        if uwf_enabled:
            cstate, estate, bstate = "disabled", "disabled", "disabled"
        else:
            cstate, estate, bstate = "readonly", "normal", "normal"
        for w in (self.cb_filter, self.cb_type, self.cb_horm):
            try:
                w.configure(state=cstate)
            except Exception:
                pass
        for w in (self.ent_max_size, self.ent_warn, self.ent_crit):
            try:
                w.configure(state=estate)
            except Exception:
                pass
        for w in (self.btn_apply_basic, self.btn_apply_cache):
            try:
                w.configure(state=bstate)
            except Exception:
                pass

    def _update_protect_table(self, vols):
        """更新分区保护表格。

        列：分区 / 当前状态 / 重启后状态
        - 当前状态：来自 WMI 的 CurrentSession（本会话实际生效状态）。
        - 重启后状态：若有待生效操作（待保护/待取消），显示「重启生效」，
          重启后实际状态变化时自动清除待生效标记。
        """
        for i in self.tree_protect.get_children():
            self.tree_protect.delete(i)
        seen = set()
        for v in vols:
            dl = v.get("DriveLetter") or "?"
            if dl in seen:
                continue
            seen.add(dl)
            cur_prot = bool(v.get("CurrentProtected"))
            cur = "已保护" if cur_prot else "未保护"
            pending = self._pending_protect.get(dl)
            if pending is True:
                nxt = "已保护（重启生效）"
                if cur_prot:  # 已真正生效，清除待标记
                    self._pending_protect.pop(dl, None)
                    nxt = "已保护"
            elif pending is False:
                nxt = "未保护（重启取消）"
                if not cur_prot:
                    self._pending_protect.pop(dl, None)
                    nxt = "未保护"
            else:
                np = v.get("NextProtected")
                nxt = ("已保护" if np else "未保护") if np is not None \
                    else ("已保护" if cur_prot else "未保护")
            self.tree_protect.insert("", "end", values=(dl, cur, nxt))

    def _refresh_exclusions_ui(self):
        """刷新排除列表 UI。"""
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            return c.get_exclusions()

        def done(exclusions):
            for i in self.tree_exc.get_children():
                self.tree_exc.delete(i)
            for ex in exclusions:
                self.tree_exc.insert("", "end", values=(
                    ex["drive"], ex["path"]))
            count = len(exclusions)
            self.lbl_exc_count.config(text=f"共 {count} 条排除项")

        def fail(m):
            self.lbl_exc_count.config(text=f"获取失败: {m}")

        self._run_com("settings_gen", op, done, fail)

    def _render_error(self, msg, _supported):
        self._render_status(None, is_error=True, msg=msg)

    # ==================== 实时自动刷新（覆盖层内存/卷列表）====================
    def _start_auto_refresh(self):
        self._auto_refresh()

    def _auto_refresh(self):
        if not self.root.winfo_exists():
            return

        def fetch():
            c = uwf_core.UWFCore()
            c.connect()
            return (c.get_filter(), c.get_volumes(),
                    c.get_overlay(), c.get_overlay_config())

        def done(data):
            try:
                self._render_realtime(data)
            except Exception:
                pass
            if self.root.winfo_exists():
                self.root.after(3000, self._auto_refresh)

        def fail(m, s):
            if self.root.winfo_exists():
                self.root.after(3000, self._auto_refresh)

        self._run_com("rt_gen", fetch, done, fail)

    def _render_realtime(self, data):
        """仅刷新：状态文字、覆盖层内存条、受保护卷列表。
        不触动设置面板，避免打断用户正在输入的阈值。"""
        flt, vols, overlay, cfg = data
        # --- 状态文字 ---
        enabled = flt.get("CurrentEnabled")
        if enabled:
            self.lbl_status.config(text="已启用", fg=GREEN)
            self.btn_toggle.config(text="关闭保护")
        else:
            self.lbl_status.config(text="已禁用", fg=RED)
            self.btn_toggle.config(text="开启保护")
        next_en = flt.get("NextEnabled")
        next_s = "启用" if next_en else "禁用" if next_en is not None else "?"
        self.lbl_mode.config(
            text=f"下次启动: {next_s}  |  "
                 f"HORM: {'开' if flt.get('HORMEnabled') else '关'}  |  "
                 f"关机待处理: {'是' if flt.get('ShutdownPending') else '否'}")
        # --- 覆盖层内存（实时）---
        if overlay:
            used_mb = overlay.get("OverlayConsumption") or 0
            avail_mb = overlay.get("AvailableSpace") or 0
            max_cfg = cfg.get("MaximumSize") or 4096
            total_mb = used_mb + avail_mb
            display_total = max(total_mb, max_cfg)
            pct = (used_mb / display_total * 100) if display_total > 0 else 0
            warn = overlay.get("WarningOverlayThreshold") or 0
            crit = overlay.get("CriticalOverlayThreshold") or 0
            self.lbl_overlay.config(
                text=f"{used_mb:.0f} MB / {display_total:.0f} MB "
                     f"(上限 {max_cfg} MB)")
            self.bar_overlay["value"] = min(pct, 100)
            bar_color = ACCENT
            if crit and used_mb >= crit:
                bar_color = RED
            elif warn and used_mb >= warn:
                bar_color = AMBER
            self.style.configure("Accent.Horizontal.TProgressbar",
                                background=bar_color)
            alert = ""
            if crit and used_mb >= crit:
                alert = "  ⚠ 已达临界阈值!"
            elif warn and used_mb >= warn:
                alert = "  ⚠ 已超警告阈值"
            self.lbl_overlay_detail.config(
                text=f"已用 {pct:.1f}%  |  可用 {avail_mb:.0f} MB{alert}")
        elif cfg.get("MaximumSize"):
            self.lbl_overlay.config(text=f"上限 {cfg['MaximumSize']} MB")
            self.lbl_overlay_detail.config(text="（无实时用量数据）")
        else:
            self.lbl_overlay.config(text="—")
            self.lbl_overlay_detail.config(text="")
        # 托盘显示剩余内存（实时）
        self._update_tray(flt, overlay)
        # --- 卷列表（实时）---
        for i in self.tree_vol.get_children():
            self.tree_vol.delete(i)
        seen = set()
        for v in vols:
            dl = v.get("DriveLetter") or "?"
            if dl in seen:
                continue
            seen.add(dl)
            prot = "已保护" if v.get("CurrentProtected") else "未保护"
            cons = v.get("OverlayConsumption")
            cons_s = f"{cons:.0f} MB" if cons else "—"
            nxt = v.get("NextProtected")
            nxt_s = ("已保护" if nxt else "未保护") if nxt is not None else "不变"
            cp = v.get("CommitPending")
            cp_s = "有" if cp else "无"
            self.tree_vol.insert("", "end", values=(
                dl, prot, cons_s, nxt_s, cp_s))

    # ==================== 操作：启用/禁用 ====================
    def on_toggle(self):
        target = "禁用" if self.lbl_status.cget("text") == "已启用" else "启用"

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            if target == "禁用":
                c.disable_filter()
            else:
                c.enable_filter()
            return target

        def done(t):
            messagebox.showinfo("成功", f"UWF 已设为{t}，重启后生效。")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 操作：提交/重启/关机 ====================
    def on_commit_all(self):
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.commit_all_deletions()
            return True

        def done(_):
            messagebox.showinfo("成功", "已提交所有删除。")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    def on_restart(self):
        if not messagebox.askyesno("确认重启", "确定要立即重启计算机吗？\n"
                                    "UWF 设置变更将在重启后生效。"):
            return

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.restart_system()
            return True

        def done(_):
            pass  # 系统正在重启

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    def on_shutdown(self):
        if not messagebox.askyesno("确认关机", "确定要关闭计算机吗？"):
            return

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.shutdown_system()
            return True

        def done(_):
            pass

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 操作：应用基本设置 ====================
    def on_apply_basic(self):
        if getattr(self, "ufw_enabled", False):
            messagebox.showwarning("不可编辑",
                "UWF 当前为启用状态，基本设置需先禁用 UWF 才能修改。\n"
                "请在「状态概览」中关闭写入过滤并重启后再设置。")
            return
        filter_val = self.var_filter.get()
        type_val = self.var_ovl_type.get()
        horm_val = self.var_horm.get()

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            results = []
            # 写入过滤
            if filter_val == "启用":
                c.enable_filter()
                results.append("写入过滤 → 启用")
            elif filter_val == "禁用":
                c.disable_filter()
                results.append("写入过滤 → 禁用")
            # 覆盖类型
            if type_val in OVERLAY_TYPES_REV:
                c.set_overlay_type(OVERLAY_TYPES_REV[type_val])
                results.append(f"覆盖类型 → {type_val}")
            # HORM
            if horm_val == "启用":
                c.enable_horm()
                results.append("HORM → 启用")
            elif horm_val == "禁用":
                c.disable_horm()
                results.append("HORM → 禁用")
            return "\n".join(results)

        def done(msg):
            messagebox.showinfo("设置已应用",
                                f"{msg}\n\n重启后生效。")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 操作：应用缓存设置 ====================
    def on_apply_cache(self):
        if getattr(self, "ufw_enabled", False):
            messagebox.showwarning("不可编辑",
                "UWF 当前为启用状态，缓存设置需先禁用 UWF 才能修改。\n"
                "请在「状态概览」中关闭写入过滤并重启后再设置。")
            return
        try:
            max_sz = int(self.ent_max_size.get())
            warn = int(self.ent_warn.get())
            crit = int(self.ent_crit.get())
        except ValueError:
            messagebox.showerror("参数错误", "三个阈值都必须是整数。")
            return

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.set_maximum_size(max_sz)
            c.set_warning_threshold(warn)
            c.set_critical_threshold(crit)
            return f"最大缓存={max_sz}MB  警告={warn}MB  严重={crit}MB"

        def done(msg):
            messagebox.showinfo("缓存设置已保存", msg)
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 操作：分区保护 ====================
    def on_protect_volume(self, drive_letter):
        self._do_protect_op(drive_letter, True)

    def on_unprotect_volume(self, drive_letter):
        self._do_protect_op(drive_letter, False)

    def _do_protect_op(self, drive_letter, protect):
        action = "保护" if protect else "取消保护"

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            if protect:
                c.protect_volume(drive_letter)
            else:
                c.unprotect_volume(drive_letter)
            return f"{drive_letter} → {action}"

        def done(msg):
            messagebox.showinfo(
                "成功", f"{msg}\n重启后生效。\n"
                        f"可在「状态概览」点「重启系统」使其立即生效。")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    def on_protect_selected(self):
        sel = self.tree_protect.selection()
        if not sel:
            return
        dl = self.tree_protect.item(sel[0], "values")[0]
        self._do_protect_op(dl, True)

    def on_unprotect_selected(self):
        sel = self.tree_protect.selection()
        if not sel:
            return
        dl = self.tree_protect.item(sel[0], "values")[0]
        self._do_protect_op(dl, False)

    def on_prot_rightclick(self, event):
        row = self.tree_protect.identify_row(event.y)
        if row:
            self.tree_protect.selection_set(row)
            self.prot_menu.tk_popup(event.x_root, event.y_root)

    # ==================== 操作：排除列表 ====================
    def on_add_exclusion(self):
        drive = self.var_exc_drive.get().rstrip(":") + ":"
        path = self.ent_exc_path.get().strip()
        if not path:
            messagebox.showwarning("输入错误", "请输入排除路径。")
            return

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.add_exclusion(drive, path)
            return f"{drive}{path}"

        def done(p):
            messagebox.showinfo("已添加", f"排除项:\n{p}")
            self._refresh_exclusions_ui()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("settings_gen", op, done, fail)

    def on_clear_exclusions(self):
        if not messagebox.askyesno("确认", "确定要清除所有排除项吗？"):
            return
        drive = self.var_exc_drive.get().rstrip(":") + ":"

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.remove_all_exclusions(drive)
            return drive

        def done(d):
            messagebox.showinfo("已清除", f"{d} 的所有排除项已清除。")
            self._refresh_exclusions_ui()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("settings_gen", op, done, fail)

    def on_remove_exc_sel(self):
        sel = self.tree_exc.selection()
        if not sel:
            return
        vals = self.tree_exc.item(sel[0], "values")
        drive, path = vals[0], vals[1]

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.remove_exclusion(drive, path)
            return f"{drive}{path}"

        def done(p):
            messagebox.showinfo("已移除", p)
            self._refresh_exclusions_ui()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("settings_gen", op, done, fail)

    def on_copy_exc_path(self):
        sel = self.tree_exc.selection()
        if not sel:
            return
        path = self.tree_exc.item(sel[0], "values")[1]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)

    def on_exc_rightclick(self, event):
        row = self.tree_exc.identify_row(event.y)
        if row:
            self.tree_exc.selection_set(row)
            self.exc_menu.tk_popup(event.x_root, event.y_root)

    def on_exc_browse_file(self):
        path = filedialog.askopenfilename(title="选择要排除的文件")
        if path:
            # 转换为相对路径
            drive = path[:2]
            rel = path[2:]
            self.var_exc_drive.set(drive)
            self.ent_exc_path.delete(0, tk.END)
            self.ent_exc_path.insert(0, rel.replace("/", "\\"))

    def on_exc_browse_dir(self):
        path = filedialog.askdirectory(title="选择要排除的文件夹")
        if path:
            drive = path[:2]
            rel = path[2:]
            self.var_exc_drive.set(drive)
            self.ent_exc_path.delete(0, tk.END)
            self.ent_exc_path.insert(0, rel.replace("/", "\\"))

    def on_import_exc(self):
        path = filedialog.askopenfilename(
            title="导入排除列表",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            imported = 0
            for line in lines:
                # 尝试解析 "C:\path" 格式
                if len(line) >= 2 and line[1] == ":":
                    drv = line[:2]
                    rp = line[2:]
                else:
                    drv = self.var_exc_drive.get()
                    rp = line
                if drv and rp:
                    try:
                        c = uwf_core.UWFCore()
                        c.connect()
                        c.add_exclusion(drv, rp)
                        imported += 1
                    except Exception:
                        pass
            messagebox.showinfo("导入完成", f"已导入 {imported} 条排除项。")
            self._refresh_exclusions_ui()
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def on_export_exc(self):
        items = [self.tree_exc.item(i, "values")
                  for i in self.tree_exc.get_children()]
        if not items:
            messagebox.showinfo("提示", "排除列表为空。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            title="导出排除列表")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for drv, p in items:
                    f.write(f"{drv}{p}\n")
            messagebox.showinfo("已导出", f"已保存 {len(items)} 条到:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def on_refresh_exc(self):
        self._refresh_exclusions_ui()

    # ==================== 文件浏览器 ====================
    def on_scan(self):
        try:
            days = int(self.var_days.get())
            min_mb = int(self.var_min.get())
        except ValueError:
            messagebox.showerror("参数错误", "天数和最小 MB 必须为数字。")
            return
        self.lbl_scan.config(text="扫描中…")
        self.root.update()

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            vols = c.get_volumes()
            protected = list(dict.fromkeys(
                v["DriveLetter"] for v in vols if v.get("CurrentProtected")))
            if not protected:
                protected = ["C:"]
            all_files = []
            for d in protected:
                try:
                    files = file_scan.scan_volume(
                        d, top_n=400, min_size_mb=min_mb, days=days,
                        timeout_sec=25)
                    all_files.extend(files)
                except Exception:
                    pass
            all_files.sort(key=lambda x: x["size_bytes"], reverse=True)
            return all_files[:500]

        def done(files):
            self._render_files(files, days, min_mb)

        def fail(m):
            self.lbl_scan.config(text=f"扫描失败: {m}")

        self._run_com("log_gen", op, done, fail)

    def _render_files(self, files, days, min_mb):
        for i in self.tree_file.get_children():
            self.tree_file.delete(i)
        for f in files:
            self.tree_file.insert("", "end", values=(
                f["path"],
                file_scan.format_size(f["size_bytes"]),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"])),
                f["ext"] or "—"))
        agg = file_scan.ext_summary(files)
        total = sum(x[1] for x in agg) or 1
        lines = []
        for ext, sz in agg[:8]:
            pct = sz / total * 100
            lines.append(f"{ext}: {file_scan.format_size(sz)} ({pct:.0f}%)")
        self.lbl_summary.config(
            text="类型占比 ▸ " + "   ".join(lines) if lines else "")
        self.lbl_scan.config(
            text=f"找到 {len(files)} 个文件（>{min_mb}MB, 近{days}天）")

    # ==================== 实时写入监控 ====================
    def _monitor_dirs(self):
        """返回要递归监控的目录（覆盖层写入最常发生的位置）。"""
        dirs = []
        for d in ("C:\\Windows", "C:\\Program Files",
                  "C:\\Program Files (x86)", "C:\\ProgramData",
                  "C:\\Users"):
            if os.path.isdir(d):
                dirs.append(d)
        return dirs

    def on_log_toggle(self):
        if not self.monitoring:
            dirs = self._monitor_dirs()
            if not dirs:
                messagebox.showwarning("无监控目录", "未找到 C: 下的可监控目录。")
                return
            self.monitor.start(dirs)
            self.monitoring = True
            self.btn_log_toggle.config(text="关闭记录")
            self.lbl_log.config(text="● 监控中…", fg=GREEN)
            self._log_pump()
        else:
            self.monitor.stop()
            self.monitoring = False
            self.btn_log_toggle.config(text="开启记录")
            self.lbl_log.config(text="已停止（记录保留）", fg=TEXT_SUB)
            self._update_log_summary()

    def _log_pump(self):
        """定时从监控队列取事件刷新界面（仅监控中调用）。"""
        if not self.monitoring:
            return
        events = self.monitor.drain(400)
        for ts, path, act, size in events:
            self._log_add_event(ts, path, act, size)
        self._update_log_summary()
        self.root.after(400, self._log_pump)

    def _log_add_event(self, ts, path, act, size):
        self.log_event_count += 1
        tstr = time.strftime("%H:%M:%S", time.localtime(ts))
        sz = file_scan.format_size(size) if size else "—"
        if path in self.log_map:
            row = self.log_map[path]
            self.tree_log.move(row, "", 0)  # 置顶（最新）
            self.tree_log.item(row, values=(tstr, path, sz, act))
        else:
            row = self.tree_log.insert("", 0, values=(tstr, path, sz, act))
            self.log_map[path] = row
            if len(self.log_map) > self.MAX_LOG_ROWS:
                old_path, old_row = self.log_map.popitem(last=False)
                self.tree_log.delete(old_row)
        self.log_records.append((ts, path, act, size))
        if len(self.log_records) > self.EXPORT_CAP:
            self.log_records.pop(0)

    def _update_log_summary(self):
        self.lbl_log_summary.config(
            text=f"本次会话已捕获 {self.log_event_count} 次写入事件 · "
                 f"列表显示最新 {len(self.log_map)} 个不同文件")

    def on_clear_log(self):
        for row in self.tree_log.get_children():
            self.tree_log.delete(row)
        self.log_map.clear()
        self.log_records.clear()
        self.log_event_count = 0
        self._update_log_summary()

    def on_export_log(self):
        if not self.log_records:
            messagebox.showinfo("提示", "暂无记录，请先「开启记录」。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            title="导出覆盖层写入记录")
        if not path:
            return
        try:
            lines = ["UWF 覆盖层实时写入记录"]
            lines.append(f"导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"监控状态: {'监控中' if self.monitoring else '已停止'}")
            lines.append(f"捕获事件总数: {self.log_event_count}  "
                         f"列表文件数: {len(self.log_map)}")
            lines.append("=" * 90)
            total = 0
            for ts, p, act, size in self.log_records:
                lines.append(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}  "
                    f"[{act}]  {p}  |  {file_scan.format_size(size)}")
                total += size or 0
            lines.append("=" * 90)
            lines.append(f"记录条数: {len(self.log_records)}  "
                         f"累计大小: {file_scan.format_size(total)}")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            messagebox.showinfo("已导出", f"记录已保存:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    @staticmethod
    def _parse_size(s):
        try:
            num, unit = s.split()
            num = float(num)
            mult = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3,
                    "TB": 1024**4}.get(unit, 1)
            return int(num * mult)
        except Exception:
            return 0

    # ==================== 右键/打开（文件浏览器）====================
    def on_open_file(self, event):
        sel = self.tree_file.selection()
        if not sel:
            return
        path = self.tree_file.item(sel[0], "values")[0]
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass

    def on_file_rightclick(self, event):
        row = self.tree_file.identify_row(event.y)
        if row:
            self.tree_file.selection_set(row)
            self.file_menu.tk_popup(event.x_root, event.y_root)

    def on_open_file_menu(self):
        self._open_selected_dir(self.tree_file, col=0)

    def on_copy_path(self):
        self._copy_selected_path(self.tree_file, col=0)

    def on_commit_delete(self):
        sel = self.tree_file.selection()
        if not sel:
            return
        path = self.tree_file.item(sel[0], "values")[0]
        drive = path[:2]
        rel = path[3:]

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.commit_file_deletion(drive, rel)
            return path

        def done(p):
            messagebox.showinfo("成功", f"已提交删除:\n{p}")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 右键/打开（日志）====================
    def on_open_log_file(self, event):
        sel = self.tree_log.selection()
        if not sel:
            return
        path = self.tree_log.item(sel[0], "values")[1]
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass

    def on_log_rightclick(self, event):
        row = self.tree_log.identify_row(event.y)
        if row:
            self.tree_log.selection_set(row)
            self.log_menu.tk_popup(event.x_root, event.y_root)

    def on_open_log_menu(self):
        self._open_selected_dir(self.tree_log, col=1)

    def on_copy_log_path(self):
        self._copy_selected_path(self.tree_log, col=1)

    def on_commit_log_delete(self):
        sel = self.tree_log.selection()
        if not sel:
            return
        path = self.tree_log.item(sel[0], "values")[1]
        drive = path[:2]
        rel = path[3:]

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.commit_file_deletion(drive, rel)
            return path

        def done(p):
            messagebox.showinfo("成功", f"已提交删除:\n{p}")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ==================== 通用辅助 ====================
    def _open_selected_dir(self, tree, col=1):
        sel = tree.selection()
        if not sel:
            return
        path = tree.item(sel[0], "values")[col]
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass

    def _copy_selected_path(self, tree, col=1):
        sel = tree.selection()
        if not sel:
            return
        path = tree.item(sel[0], "values")[col]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)

    # ==================== 窗口关闭（最小化到托盘）====================
    def on_close(self):
        self.tray.hide_main()

    # ==================== Tab 5: 注册表排除（与 UWFPRO 对齐）====================
    def _build_tab_registry(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 上半部分：添加排除
        add_frame = tk.Frame(top, bg=BG)
        add_frame.pack(fill=tk.X, pady=(0, 8))
        inner = self._card(add_frame, "添加注册表排除项")
        grid = tk.Frame(inner, bg=CARD_BG)
        grid.pack(fill=tk.X)
        tk.Label(grid, text="注册表项:", bg=CARD_BG, font=FONT).grid(
            row=0, column=0, sticky="e", padx=4, pady=5)
        self.var_reg_key = tk.StringVar()
        ttk.Entry(grid, textvariable=self.var_reg_key, width=60).grid(
            row=0, column=1, sticky="we", padx=4, pady=5)
        grid.columnconfigure(1, weight=1)
        tk.Label(grid, text="(完整路径, 如 HKLM\\SOFTWARE\\Microsoft\\Windows\\"
                 "CurrentVersion\\Run)", bg=CARD_BG, font=("Segoe UI", 8),
                 fg=TEXT_SUB).grid(row=1, column=1, sticky="w", padx=4)

        reg_btns = tk.Frame(inner, bg=CARD_BG)
        reg_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(reg_btns, text="添加排除", width=12,
                   command=self.on_add_reg_exclusion).pack(side=tk.LEFT, padx=2)
        ttk.Button(reg_btns, text="提交注册表值", width=14,
                   command=self.on_commit_reg).pack(side=tk.LEFT, padx=2)
        ttk.Button(reg_btns, text="导入列表", width=12,
                   command=self.on_import_reg).pack(side=tk.LEFT, padx=2)
        ttk.Button(reg_btns, text="导出列表", width=12,
                   command=self.on_export_reg).pack(side=tk.LEFT, padx=2)

        # 下半部分：排除列表
        inner = self._card(top, "注册表排除列表（下次会话生效 · 右键可删除）")
        reg_cols = ("注册表项",)
        self.tree_reg = ttk.Treeview(inner, columns=reg_cols, show="headings",
                                     height=15)
        self.tree_reg.heading("注册表项", text="注册表项")
        self.tree_reg.column("注册表项", width=760)
        self.tree_reg.pack(fill=tk.BOTH, expand=True, pady=4)
        self.reg_menu = tk.Menu(self.root, tearoff=0)
        self.reg_menu.add_command(label="删除选中",
                                 command=self.on_remove_reg_sel)
        self.tree_reg.bind("<Button-3>", self.on_reg_rightclick)

        reg_bot = tk.Frame(inner, bg=CARD_BG)
        reg_bot.pack(fill=tk.X, pady=(4, 0))
        self.lbl_reg_count = tk.Label(reg_bot, text="", bg=CARD_BG, fg=TEXT_SUB,
                                      font=FONT)
        self.lbl_reg_count.pack(side=tk.LEFT)
        ttk.Button(reg_bot, text="刷新列表", width=12,
                   command=self.on_refresh_reg).pack(side=tk.RIGHT, padx=2)

    def _refresh_registry_ui(self):
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            return c.get_registry_exclusions()
        def done(keys):
            for i in self.tree_reg.get_children():
                self.tree_reg.delete(i)
            for k in keys:
                self.tree_reg.insert("", "end", values=(k,))
            self.lbl_reg_count.config(text=f"共 {len(keys)} 条注册表排除项")
        def fail(m):
            self.lbl_reg_count.config(text=f"获取失败: {m}")
        self._run_com("reg_gen", op, done, fail)

    def on_refresh_reg(self):
        self._refresh_registry_ui()

    def on_add_reg_exclusion(self):
        key = self.var_reg_key.get().strip()
        if not key:
            messagebox.showwarning("提示", "请输入注册表项路径。")
            return
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.add_registry_exclusion(key)
            return key
        def done(k):
            messagebox.showinfo("成功", f"已添加注册表排除:\n{k}\n重启后生效。")
            self._refresh_registry_ui()
        def fail(m):
            messagebox.showerror("失败", m)
        self._run_com("reg_gen", op, done, fail)

    def on_remove_reg_sel(self):
        sel = self.tree_reg.selection()
        if not sel:
            return
        key = self.tree_reg.item(sel[0], "values")[0]
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.remove_registry_exclusion(key)
            return key
        def done(k):
            messagebox.showinfo("成功", f"已删除注册表排除:\n{k}")
            self._refresh_registry_ui()
        def fail(m):
            messagebox.showerror("失败", m)
        self._run_com("reg_gen", op, done, fail)

    def on_reg_rightclick(self, event):
        if self.tree_reg.selection():
            self.reg_menu.post(event.x_root, event.y_root)

    def on_commit_reg(self):
        key = self.var_reg_key.get().strip()
        if not key:
            messagebox.showwarning("提示", "请输入要提交的注册表项路径。")
            return
        value = simpledialog.askstring(
            "提交注册表值",
            f"输入要提交的值名称（针对 {key}）：\n留空则提交整个项。")
        if value is None:
            return
        if not value.strip():
            messagebox.showwarning("提示", "提交注册表值需要填写值名称。")
            return
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.commit_registry(key, value.strip())
            return key
        def done(k):
            messagebox.showinfo("成功", f"已提交注册表更改:\n{k}")
        def fail(m):
            messagebox.showerror("失败", m)
        self._run_com("reg_gen", op, done, fail)

    def on_import_reg(self):
        path = filedialog.askopenfilename(filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                keys = [l.strip() for l in f if l.strip()]
        except Exception as e:
            messagebox.showerror("导入失败", str(e))
            return
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            added = []
            for k in keys:
                try:
                    c.add_registry_exclusion(k)
                    added.append(k)
                except Exception:
                    pass
            return added
        def done(added):
            messagebox.showinfo("导入完成", f"成功导入 {len(added)}/{len(keys)} 条。")
            self._refresh_registry_ui()
        def fail(m):
            messagebox.showerror("失败", m)
        self._run_com("reg_gen", op, done, fail)

    def on_export_reg(self):
        keys = [self.tree_reg.item(i, "values")[0]
                for i in self.tree_reg.get_children()]
        if not keys:
            messagebox.showwarning("提示", "当前没有可导出的注册表排除项。")
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("文本文件", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for k in keys:
                    f.write(k + "\n")
            messagebox.showinfo("导出完成", f"已导出 {len(keys)} 条到:\n{path}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ==================== 服务模式 ====================
    def on_apply_servicing(self):
        val = self.var_servicing.get()
        enable = (val == "启用")
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.set_servicing(enable)
            return enable
        def done(en):
            messagebox.showinfo("服务模式",
                                f"已设为{'启用' if en else '禁用'}，重启后生效。")
            self.refresh()
        def fail(m):
            messagebox.showerror("失败", m)
        self._run_com("svc_gen", op, done, fail)

    # ==================== 覆盖层文件（只读）====================
    def on_refresh_overlay_files(self):
        drive = self.var_ovf_drive.get()
        def op():
            c = uwf_core.UWFCore()
            c.connect()
            return c.get_overlay_files(drive)
        def done(files):
            for i in self.tree_ovf.get_children():
                self.tree_ovf.delete(i)
            for f in files:
                self.tree_ovf.insert(
                    "", "end", values=(f["path"], human_size(f["size"])))
            self.lbl_ovf_count.config(text=f"共 {len(files)} 个文件")
        def fail(m):
            self.lbl_ovf_count.config(text=f"获取失败: {m}")
        self._run_com("ovf_gen", op, done, fail)


# ==================== 入口 ====================
def main():
    if "--check" in sys.argv:
        log_path = os.path.join(os.path.dirname(
            os.path.abspath(sys.argv[0])), "uwf_check.log")
        lines = []
        try:
            pythoncom.CoInitialize()
            c = uwf_core.UWFCore()
            c.connect()
            flt = c.get_filter()
            ov = c.get_overlay()
            cfg = c.get_overlay_config()
            lines.append("UWF CONNECT OK")
            lines.append(f"  Enabled={flt['CurrentEnabled']}  "
                         f"OverlayUsed={ov.get('OverlayConsumption')}MB  "
                         f"MaxSize={cfg.get('MaximumSize')}MB")
            excl = c.get_exclusions()
            lines.append(f"  Exclusions={len(excl)}")
            lines.append("RESULT=PASS")
        except Exception as e:
            lines.append(f"UWF CHECK FAILED: {e}")
            lines.append("RESULT=FAIL")
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        sys.exit(0 if lines[-1] == "RESULT=PASS" else 1)

    root = tk.Tk()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        pythoncom.CoInitialize()
    except Exception:
        pass
    app = UWFApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
