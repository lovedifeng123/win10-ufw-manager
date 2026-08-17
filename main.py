"""
UWF Manager Pro - 主程序（tkinter UI）
功能：
  1. UWF 状态面板：启用/禁用状态、当前模式、覆盖使用量
  2. 受保护卷列表
  3. 文件浏览器：扫描受保护卷上最近修改的大文件，定位覆盖层占用来源
  4. 覆盖层文件日志：列出自本次开机以来被写入/修改、实际暂存在 UWF
     内存(覆盖层)中的文件，含原始 C 盘路径、大小、类型、总数与总大小
  5. 操作：启用/禁用 UWF、提交删除、设置覆盖上限

关键修复：所有 WMI/COM 访问都在「带 pythoncom.CoInitialize 的独立线程」
中执行，并返回纯 dict 给主线程渲染。绝不在主线程或跨线程复用 COM 对象，
否则 winmgmts 查询会挂死（表现为一直"检测中"）。
"""
import sys
import os
import time
import threading
import pythoncom
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import uwf_core
import file_scan
import ctypes  # 取开机时间，作为"覆盖层会话"起点

# Windows 主题色
ACCENT = "#0078D4"          # 微软蓝
ACCENT_DARK = "#005A9E"
BG = "#F3F3F3"
CARD_BG = "#FFFFFF"
TEXT = "#1A1A1A"
TEXT_SUB = "#666666"
GREEN = "#107C10"
RED = "#D13438"
AMBER = "#FF8C00"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_BIG = ("Segoe UI", 26, "bold")


def boot_time_epoch():
    """返回本次开机时间（epoch 秒），作为 UWF 覆盖层会话起点。"""
    try:
        uptime_ms = ctypes.windll.kernel32.GetTickCount64()
        return time.time() - uptime_ms / 1000.0
    except Exception:
        return time.time() - 86400  # 兜底：近 1 天


class UWFApp:
    def __init__(self, root):
        self.root = root
        self.admin = self._check_admin()
        # 代际计数器：丢弃过期(慢/迟到)的后台回调，避免覆盖新数据
        self.status_gen = 0
        self.log_gen = 0
        self._rendered = False  # 看门狗用：状态是否已成功渲染
        self._setup_ui()
        # 初始加载
        self.refresh()
        self.generate_log(auto=True)

    # ---------------- 管理员检测 ----------------
    def _check_admin(self):
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # ---------------- 通用 COM 线程执行器 ----------------
    def _run_com(self, gen_attr, fn, on_success, on_fail):
        """在带 CoInitialize 的线程中执行 fn()，结果回到主线程回调。
        gen_attr: 'status_gen' / 'log_gen'，用于丢弃过期回调。"""
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
                    self.root.after(0, lambda: on_fail(f"刷新失败: {e}", True))
            finally:
                pythoncom.CoUninitialize()

        threading.Thread(target=work, daemon=True).start()

    # ---------------- UI 布局 ----------------
    def _setup_ui(self):
        self.root.title("UWF Manager Pro")
        self.root.geometry("1000x760")
        self.root.configure(bg=BG)
        self.root.minsize(840, 620)
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Accent.Horizontal.TProgressbar",
                             background=ACCENT, troughcolor="#E0E0E0",
                             borderwidth=0, thickness=18)
        self.style.configure("Log.Horizontal.TProgressbar",
                             background=ACCENT, troughcolor="#E0E0E0",
                             borderwidth=0, thickness=10)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # 标题栏
        title_bar = tk.Frame(self.root, bg=ACCENT, height=56)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        tk.Label(title_bar, text="UWF Manager Pro",
                 font=FONT_TITLE, fg="white", bg=ACCENT).pack(
            side=tk.LEFT, padx=18, pady=10)
        self.lbl_admin = tk.Label(title_bar, text="",
                                  font=FONT_BOLD, fg="white", bg=ACCENT)
        self.lbl_admin.pack(side=tk.RIGHT, padx=18)

        # 主滚动区域
        canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(self.root, orient="vertical",
                               command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.content = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>",
                          lambda e: canvas.configure(
                              scrollregion=canvas.bbox("all")))
        self.canvas = canvas
        self._bind_mousewheel(canvas)

        self._build_content()

    def _bind_mousewheel(self, canvas):
        def on_scroll(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", on_scroll)

    def _card(self, parent, title):
        card = tk.Frame(parent, bg=CARD_BG, relief=tk.RAISED, borderwidth=1)
        card.pack(fill=tk.X, padx=16, pady=8)
        tk.Label(card, text=title, font=FONT_BOLD, fg=TEXT_SUB,
                 bg=CARD_BG).pack(anchor="w", padx=14, pady=(10, 4))
        inner = tk.Frame(card, bg=CARD_BG)
        inner.pack(fill=tk.X, padx=14, pady=(0, 12))
        return inner

    def _build_content(self):
        # 状态卡片
        inner = self._card(self.content, "UWF 状态")
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
        self.btn_toggle = ttk.Button(btn_row, text="启用 UWF",
                                     command=self.on_toggle, width=16)
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="提交所有删除", width=16,
                   command=self.on_commit_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_row, text="刷新", width=10,
                   command=self.refresh).pack(side=tk.LEFT, padx=4)

        # 内存 / 覆盖卡片
        inner = self._card(self.content, "覆盖层内存使用")
        self.lbl_overlay = tk.Label(inner, text="—", font=FONT_BIG,
                                    fg=ACCENT, bg=CARD_BG)
        self.lbl_overlay.pack(anchor="w")
        self.bar_overlay = ttk.Progressbar(inner, length=440,
                                           mode="determinate")
        self.bar_overlay.pack(fill=tk.X, pady=(6, 4))
        self.lbl_overlay_detail = tk.Label(inner, text="", font=FONT,
                                           fg=TEXT_SUB, bg=CARD_BG)
        self.lbl_overlay_detail.pack(anchor="w")

        # 卷列表
        inner = self._card(self.content, "受保护卷")
        cols = ("盘符", "保护状态", "覆盖占用", "会话")
        self.tree_vol = ttk.Treeview(inner, columns=cols, show="headings",
                                     height=4)
        for c in cols:
            self.tree_vol.heading(c, text=c)
        self.tree_vol.column("盘符", width=60)
        self.tree_vol.column("保护状态", width=100)
        self.tree_vol.column("覆盖占用", width=140)
        self.tree_vol.column("会话", width=80)
        self.tree_vol.pack(fill=tk.X, pady=4)

        # 文件浏览器
        inner = self._card(self.content, "文件浏览器（覆盖层占用来源分析）")
        tk.Label(inner, text="扫描受保护卷上最近修改的大文件，定位覆盖内存去向。",
                 font=FONT, fg=TEXT_SUB, bg=CARD_BG).pack(anchor="w")
        opt = tk.Frame(inner, bg=CARD_BG)
        opt.pack(fill=tk.X, pady=(6, 2))
        tk.Label(opt, text="天数:", bg=CARD_BG, font=FONT).pack(side=tk.LEFT)
        self.var_days = tk.StringVar(value="30")
        ttk.Combobox(opt, textvariable=self.var_days, width=6,
                     values=("7", "14", "30", "90")).pack(side=tk.LEFT, padx=4)
        tk.Label(opt, text="最小 MB:", bg=CARD_BG, font=FONT).pack(side=tk.LEFT, padx=(8, 0))
        self.var_min = tk.StringVar(value="10")
        ttk.Entry(opt, textvariable=self.var_min, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Button(opt, text="扫描", width=10,
                   command=self.on_scan).pack(side=tk.LEFT, padx=8)
        self.lbl_scan = tk.Label(opt, text="", bg=CARD_BG, fg=TEXT_SUB,
                                 font=FONT)
        self.lbl_scan.pack(side=tk.LEFT, padx=8)

        file_cols = ("路径", "大小", "修改时间", "类型")
        self.tree_file = ttk.Treeview(inner, columns=file_cols,
                                      show="headings", height=10)
        for c in file_cols:
            self.tree_file.heading(c, text=c)
        self.tree_file.column("路径", width=460)
        self.tree_file.column("大小", width=90)
        self.tree_file.column("修改时间", width=140)
        self.tree_file.column("类型", width=60)
        self.tree_file.pack(fill=tk.X, pady=4)
        self.tree_file.bind("<Double-1>", self.on_open_file)
        self.file_menu = tk.Menu(self.root, tearoff=0)
        self.file_menu.add_command(label="打开文件所在位置",
                                   command=self.on_open_file_menu)
        self.file_menu.add_command(label="复制路径",
                                   command=self.on_copy_path)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="提交删除（穿透覆盖层）",
                                   command=self.on_commit_delete)
        self.tree_file.bind("<Button-3>", self.on_file_rightclick)

        self.lbl_summary = tk.Label(inner, text="", font=FONT, fg=ACCENT,
                                    bg=CARD_BG, justify=tk.LEFT, wraplength=900)
        self.lbl_summary.pack(anchor="w", pady=(4, 0))

        # ===== 覆盖层文件日志（新增）=====
        inner = self._card(self.content, "覆盖层文件日志（内存中的文件）")
        tk.Label(inner,
                 text="列出自「本次开机」以来被写入/修改、实际暂存在 UWF "
                      "覆盖层(内存)中的文件。\n重启将丢失，除非先「提交」。"
                      "这些文件原始位置都在受保护的 C: 盘，现占用内存。",
                 font=FONT, fg=TEXT_SUB, bg=CARD_BG, justify=tk.LEFT,
                 wraplength=900).pack(anchor="w")
        opt2 = tk.Frame(inner, bg=CARD_BG)
        opt2.pack(fill=tk.X, pady=(6, 2))
        tk.Label(opt2, text="最小 MB:", bg=CARD_BG, font=FONT).pack(side=tk.LEFT)
        self.var_log_min = tk.StringVar(value="1")
        ttk.Entry(opt2, textvariable=self.var_log_min, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Button(opt2, text="生成日志", width=12,
                   command=lambda: self.generate_log(auto=False)).pack(side=tk.LEFT, padx=8)
        ttk.Button(opt2, text="导出 TXT", width=12,
                   command=self.on_export_log).pack(side=tk.LEFT, padx=4)
        self.lbl_log = tk.Label(opt2, text="", bg=CARD_BG, fg=TEXT_SUB,
                                font=FONT)
        self.lbl_log.pack(side=tk.LEFT, padx=8)

        log_cols = ("状态", "路径(原始C盘位置)", "大小", "修改时间", "类型")
        self.tree_log = ttk.Treeview(inner, columns=log_cols,
                                     show="headings", height=10)
        for c in log_cols:
            self.tree_log.heading(c, text=c)
        self.tree_log.column("状态", width=110)
        self.tree_log.column("路径(原始C盘位置)", width=470)
        self.tree_log.column("大小", width=90)
        self.tree_log.column("修改时间", width=140)
        self.tree_log.column("类型", width=60)
        self.tree_log.pack(fill=tk.X, pady=4)
        self.tree_log.bind("<Double-1>", self.on_open_log_file)
        self.log_menu = tk.Menu(self.root, tearoff=0)
        self.log_menu.add_command(label="打开文件所在位置",
                                  command=self.on_open_log_menu)
        self.log_menu.add_command(label="复制路径",
                                  command=self.on_copy_log_path)
        self.tree_log.bind("<Button-3>", self.on_log_rightclick)

        self.lbl_log_summary = tk.Label(inner, text="", font=FONT_BOLD,
                                        fg=ACCENT, bg=CARD_BG, justify=tk.LEFT,
                                        wraplength=900)
        self.lbl_log_summary.pack(anchor="w", pady=(4, 0))

        # 底部信息
        self.lbl_msg = tk.Label(self.content, text="", font=FONT,
                                fg=TEXT_SUB, bg=BG)
        self.lbl_msg.pack(anchor="w", padx=16, pady=(4, 12))

    # ---------------- 状态刷新 ----------------
    def refresh(self):
        self.lbl_status.config(text="检测中…", fg=TEXT_SUB)
        self._rendered = False
        # 看门狗：10 秒仍未渲染则提示超时
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
                text="错误: 10 秒内未收到数据。请确认：① 右键以管理员运行；"
                     "② 系统支持 UWF（embedded 命名空间）。可点击「刷新」重试。")

    def _render_status(self, data, is_error=False, msg=""):
        if is_error:
            self._rendered = True
            self.lbl_status.config(text="不可用", fg=RED)
            self.lbl_mode.config(text="")
            self.lbl_msg.config(text=f"错误: {msg}")
            self.lbl_admin.config(
                text="管理员" if self.admin else "非管理员")
            if not is_error or True:
                messagebox.showerror("UWF 状态", msg)
            return

        self._rendered = True
        flt, vols, overlay, cfg = data
        enabled = flt.get("CurrentEnabled")
        if enabled:
            self.lbl_status.config(text="已启用", fg=GREEN)
            self.btn_toggle.config(text="禁用 UWF")
        else:
            self.lbl_status.config(text="已禁用", fg=RED)
            self.btn_toggle.config(text="启用 UWF")
        self.lbl_mode.config(
            text=f"关机待处理: {'是' if flt.get('ShutdownPending') else '否'}  |  "
                 f"HORM: {'开' if flt.get('HORMEnabled') else '关'}")
        self.lbl_admin.config(
            text="管理员" if self.admin else "非管理员(需右键以管理员运行)")

        if overlay:
            used = overlay.get("OverlayConsumption") or 0
            avail = overlay.get("AvailableSpace") or 0
            total = used + avail
            pct = (used / total * 100) if total > 0 else 0
            warn = overlay.get("WarningOverlayThreshold") or 0
            crit = overlay.get("CriticalOverlayThreshold") or 0
            self.lbl_overlay.config(
                text=f"{file_scan.format_size(used)} / "
                     f"{file_scan.format_size(total)}")
            self.bar_overlay["value"] = pct
            bar_color = ACCENT
            if crit and used >= crit:
                bar_color = RED
            elif warn and used >= warn:
                bar_color = AMBER
            self.style.configure("Accent.Horizontal.TProgressbar",
                                background=bar_color)
            alert = ""
            if crit and used >= crit:
                alert = " ⚠ 已达临界阈值！"
            elif warn and used >= warn:
                alert = " ⚠ 已超过警告阈值"
            self.lbl_overlay_detail.config(
                text=f"已用 {pct:.1f}%  | 可用 "
                     f"{file_scan.format_size(avail)}{alert}")
        elif cfg.get("MaximumSize"):
            self.lbl_overlay.config(
                text=f"上限 {cfg['MaximumSize']} MB")
            self.lbl_overlay_detail.config(
                text="（系统未提供实时用量）")
        else:
            self.lbl_overlay.config(text="—")
            self.lbl_overlay_detail.config(text="")

        for i in self.tree_vol.get_children():
            self.tree_vol.delete(i)
        seen_vol = set()
        for v in vols:
            dl = v.get("DriveLetter") or "?"
            if dl in seen_vol:
                continue
            seen_vol.add(dl)
            prot = "已保护" if v.get("Protected") else "未保护"
            cons = v.get("OverlayConsumption")
            cons_s = file_scan.format_size(cons) if cons else "—"
            sess = v.get("CurrentSession")
            sess_s = str(sess) if sess is not None else "—"
            self.tree_vol.insert("", "end", values=(dl, prot, cons_s, sess_s))

        self.lbl_msg.config(
            text=f"最后刷新: {time.strftime('%H:%M:%S')}  |  "
                 f"命名空间 root\\standardcimv2\\embedded 正常")

    def _render_error(self, msg, _supported):
        self._render_status(None, is_error=True, msg=msg)

    # ---------------- 操作 ----------------
    def on_toggle(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return
        target = "禁用" if self.lbl_status.cget("text") == "已启用" else "启用"

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            if target == "禁用":
                c.disable()
            else:
                c.enable()
            return target

        def done(t):
            messagebox.showinfo("操作成功",
                                f"UWF 已设为{target}，重启后生效。\n"
                                f"（请重启计算机完成切换）")
            self.refresh()

        def fail(m):
            messagebox.showerror("操作失败", m)

        self._run_com("status_gen", op, done, fail)

    def on_commit_all(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            c.commit_all_deletions()
            return True

        def done(_):
            messagebox.showinfo("成功", "已提交所有删除操作。")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ---------------- 文件浏览器 ----------------
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
            # 去重：UWF_Volume 可能返回重复盘符
            protected = list(dict.fromkeys(
                v["DriveLetter"] for v in vols if v.get("Protected")))
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
        for ext, sz in agg[:6]:
            pct = sz / total * 100
            lines.append(f"{ext}: {file_scan.format_size(sz)} ({pct:.0f}%)")
        self.lbl_summary.config(
            text="类型占比 ▸ " + "   ".join(lines) if lines else "")
        self.lbl_scan.config(
            text=f"找到 {len(files)} 个文件（>{min_mb}MB, 近{days}天）")

    # ---------------- 覆盖层文件日志（新增）----------------
    def generate_log(self, auto=False):
        try:
            min_mb = int(self.var_log_min.get())
        except ValueError:
            min_mb = 1
        if not auto:
            self.lbl_log.config(text="生成中…")
            self.root.update()

        def op():
            c = uwf_core.UWFCore()
            c.connect()
            vols = c.get_volumes()
            protected = list(dict.fromkeys(
                v["DriveLetter"] for v in vols if v.get("Protected")))
            if not protected:
                protected = ["C:"]
            boot = boot_time_epoch()
            days_since_boot = max(0.01, (time.time() - boot) / 86400.0)
            all_files = []
            for d in protected:
                try:
                    files = file_scan.scan_volume(
                        d, top_n=3000, min_size_mb=min_mb,
                        days=days_since_boot, timeout_sec=25)
                    all_files.extend(files)
                except Exception:
                    pass
            all_files.sort(key=lambda x: x["size_bytes"], reverse=True)
            return all_files[:2000]

        def done(files):
            self._render_log(files, min_mb)

        def fail(m):
            self.lbl_log.config(text=f"生成失败: {m}")

        self._run_com("log_gen", op, done, fail)

    def _render_log(self, files, min_mb):
        for i in self.tree_log.get_children():
            self.tree_log.delete(i)
        total_bytes = 0
        for f in files:
            total_bytes += f["size_bytes"]
            self.tree_log.insert("", "end", values=(
                "覆盖层(内存)",
                f["path"],
                file_scan.format_size(f["size_bytes"]),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"])),
                f["ext"] or "—"))
        self.lbl_log_summary.config(
            text=f"共 {len(files)} 个文件在内存中，合计 "
                 f"{file_scan.format_size(total_bytes)}（最小 {min_mb}MB）")
        self.lbl_log.config(
            text=f"已生成（自开机以来写入覆盖层的大文件）")

    def on_export_log(self):
        if not self.tree_log.get_children():
            messagebox.showinfo("提示", "请先点击「生成日志」。")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            title="导出覆盖层文件日志")
        if not path:
            return
        try:
            lines = []
            lines.append("UWF 覆盖层文件日志（内存中的文件）")
            lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            lines.append(f"说明: 以下文件自本次开机以来被写入/修改，"
                         f"实际暂存在 UWF 覆盖层(内存)，重启将丢失。")
            lines.append("=" * 80)
            total = 0
            for row in self.tree_log.get_children():
                vals = self.tree_log.item(row, "values")
                status, fpath, size, mtime, ext = vals
                lines.append(f"[{status}] {fpath}  |  {size}  |  {mtime}  |  {ext}")
                # 累加字节
                b = self._parse_size(size)
                total += b
            lines.append("=" * 80)
            lines.append(f"文件总数: {len(self.tree_log.get_children())}  "
                         f"合计: {file_scan.format_size(total)}")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            messagebox.showinfo("已导出", f"日志已保存:\n{path}")
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

    # ---------------- 文件浏览器右键/打开 ----------------
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
        self._open_selected_dir(self.tree_file)

    def on_copy_path(self):
        self._copy_selected_path(self.tree_file)

    def on_commit_delete(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return
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

        def done(_):
            messagebox.showinfo("成功",
                                f"已提交删除（穿透覆盖层）:\n{path}")
            self.refresh()

        def fail(m):
            messagebox.showerror("失败", m)

        self._run_com("status_gen", op, done, fail)

    # ---------------- 日志右键/打开 ----------------
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
        self._open_selected_dir(self.tree_log)

    def on_copy_log_path(self):
        self._copy_selected_path(self.tree_log)

    # ---------------- 通用辅助 ----------------
    def _open_selected_dir(self, tree):
        sel = tree.selection()
        if not sel:
            return
        col = 1 if tree is self.tree_log else 0
        path = tree.item(sel[0], "values")[col]
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass

    def _copy_selected_path(self, tree):
        sel = tree.selection()
        if not sel:
            return
        col = 1 if tree is self.tree_log else 0
        path = tree.item(sel[0], "values")[col]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)


def main():
    # 无界面自检模式：验证打包后的 exe 能正常访问 UWF
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
            vols = c.get_volumes()
            lines.append("UWF CONNECT OK")
            lines.append(f"  Enabled={flt['CurrentEnabled']}  "
                         f"OverlayUsed={ov.get('OverlayConsumption')}MB")
            lines.append(f"  Volumes={[v['DriveLetter'] for v in vols]}")
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
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
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
