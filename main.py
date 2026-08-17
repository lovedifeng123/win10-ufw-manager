"""
UWF Manager Pro - 主程序（tkinter UI）
功能：
  1. UWF 状态面板：启用/禁用状态、当前模式、覆盖使用量
  2. 受保护卷列表：各卷保护状态与覆盖占用
  3. 文件浏览器：扫描受保护卷上最近修改的大文件，定位覆盖层占用来源
  4. 操作：启用/禁用 UWF、提交删除、设置覆盖上限
"""
import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import uwf_core
import file_scan

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


class UWFApp:
    def __init__(self, root):
        self.root = root
        self.core = uwf_core.UWFCore()
        self.admin = self._check_admin()
        self._setup_ui()
        # 启动后台刷新
        self.refresh()

    # ---------------- 管理员检测 ----------------
    def _check_admin(self):
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    # ---------------- UI 布局 ----------------
    def _setup_ui(self):
        self.root.title("UWF Manager Pro")
        self.root.geometry("960x680")
        self.root.configure(bg=BG)
        self.root.minsize(820, 600)
        # 自定义进度条样式
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("Accent.Horizontal.TProgressbar",
                            background=ACCENT, troughcolor="#E0E0E0",
                            borderwidth=0, thickness=18)
        try:
            self.root.iconbitmap(default="")  # 无图标则留空
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

        self._build_content()

    def _card(self, parent, title):
        """创建卡片容器，返回内部 frame"""
        card = tk.Frame(parent, bg=CARD_BG, relief=tk.RAISED,
                        borderwidth=1)
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
        self.bar_overlay = ttk.Progressbar(inner, length=400,
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
        self.tree_file.column("路径", width=440)
        self.tree_file.column("大小", width=90)
        self.tree_file.column("修改时间", width=140)
        self.tree_file.column("类型", width=60)
        self.tree_file.pack(fill=tk.X, pady=4)
        self.tree_file.bind("<Double-1>", self.on_open_file)
        # 右键菜单：打开位置 / 复制路径 / 提交删除
        self.file_menu = tk.Menu(self.root, tearoff=0)
        self.file_menu.add_command(label="打开文件所在位置",
                                   command=self.on_open_file_menu)
        self.file_menu.add_command(label="复制路径",
                                   command=self.on_copy_path)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="提交删除（穿透覆盖层）",
                                   command=self.on_commit_delete)
        self.tree_file.bind("<Button-3>", self.on_file_rightclick)

        # 类型聚合汇总
        self.lbl_summary = tk.Label(inner, text="", font=FONT, fg=ACCENT,
                                    bg=CARD_BG, justify=tk.LEFT)
        self.lbl_summary.pack(anchor="w", pady=(4, 0))

        # 底部信息
        self.lbl_msg = tk.Label(self.content, text="", font=FONT,
                                fg=TEXT_SUB, bg=BG)
        self.lbl_msg.pack(anchor="w", padx=16, pady=(4, 12))

    # ---------------- 刷新逻辑 ----------------
    def refresh(self):
        """主线程安全地刷新所有数据"""
        def work():
            try:
                if not self.core.connected:
                    self.core.connect()
                flt = self.core.get_filter()
                vols = self.core.get_volumes()
                overlay = self.core.get_overlay()
                cfg = self.core.get_overlay_config()

                self.root.after(0, lambda: self._render_status(
                    flt, vols, overlay, cfg))
            except uwf_core.UWFNotSupported as e:
                self.root.after(0, lambda: self._render_error(str(e)))
            except Exception as e:
                self.root.after(0, lambda: self._render_error(
                    f"刷新失败: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _render_status(self, flt, vols, overlay, cfg):
        enabled = flt.get("CurrentEnabled")
        # 状态标签
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

        # 覆盖使用
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
            # 颜色根据阈值变化
            bar_color = ACCENT
            if crit and used >= crit:
                bar_color = RED
            elif warn and used >= warn:
                bar_color = AMBER
            self.bar_overlay.configure(style="Accent.Horizontal.TProgressbar")
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

        # 卷列表
        for i in self.tree_vol.get_children():
            self.tree_vol.delete(i)
        for v in vols:
            dl = v.get("DriveLetter") or "?"
            prot = "已保护" if v.get("Protected") else "未保护"
            cons = v.get("OverlayConsumption")
            cons_s = file_scan.format_size(cons) if cons else "—"
            sess = v.get("CurrentSession")
            sess_s = str(sess) if sess is not None else "—"
            self.tree_vol.insert("", "end", values=(
                dl, prot, cons_s, sess_s))

        self.lbl_msg.config(
            text=f"最后刷新: {time.strftime('%H:%M:%S')}  |  "
                 f"命名空间 root\\standardcimv2\\embedded 正常")

    def _render_error(self, msg):
        self.lbl_status.config(text="不可用", fg=RED)
        self.lbl_mode.config(text="")
        self.lbl_msg.config(text=f"错误: {msg}")
        self.lbl_admin.config(
            text="管理员" if self.admin else "非管理员")
        messagebox.showerror("UWF 状态", msg)

    # ---------------- 操作 ----------------
    def on_toggle(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return
        try:
            if self.lbl_status.cget("text") == "已启用":
                self.core.disable()
                msg = "UWF 已设为禁用，重启后生效。"
            else:
                self.core.enable()
                msg = "UWF 已设为启用，重启后生效。"
            messagebox.showinfo("操作成功", msg + "\n（请重启计算机完成切换）")
            self.refresh()
        except uwf_core.UWFError as e:
            messagebox.showerror("操作失败", str(e))

    def on_commit_all(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return
        try:
            self.core.commit_all_deletions()
            messagebox.showinfo("成功", "已提交所有删除操作。")
            self.refresh()
        except uwf_core.UWFError as e:
            messagebox.showerror("失败", str(e))

    def on_scan(self):
        try:
            days = int(self.var_days.get())
            min_mb = int(self.var_min.get())
        except ValueError:
            messagebox.showerror("参数错误", "天数和最小 MB 必须为数字。")
            return

        # 找出受保护卷
        vols = self.core.get_volumes() if self.core.connected else []
        protected = [v["DriveLetter"] for v in vols if v.get("Protected")]
        if not protected:
            protected = ["C:"]

        self.lbl_scan.config(text="扫描中…")
        self.root.update()

        def work():
            all_files = []
            for d in protected:
                try:
                    files = file_scan.scan_volume(
                        d, top_n=400, min_size_mb=min_mb, days=days)
                    all_files.extend(files)
                except Exception:
                    pass
            all_files.sort(key=lambda x: x["size_bytes"], reverse=True)
            self.root.after(0, lambda: self._render_files(
                all_files[:500], days, min_mb))

        threading.Thread(target=work, daemon=True).start()

    def _render_files(self, files, days, min_mb):
        for i in self.tree_file.get_children():
            self.tree_file.delete(i)
        for f in files:
            self.tree_file.insert("", "end", values=(
                f["path"],
                file_scan.format_size(f["size_bytes"]),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(f["mtime"])),
                f["ext"] or "—"))
        # 按类型聚合
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
        self._open_selected_dir()

    def on_copy_path(self):
        sel = self.tree_file.selection()
        if not sel:
            return
        path = self.tree_file.item(sel[0], "values")[0]
        self.root.clipboard_clear()
        self.root.clipboard_append(path)

    def on_commit_delete(self):
        if not self.admin:
            messagebox.showwarning("权限不足",
                                   "请右键本程序选择'以管理员身份运行'。")
            return
        sel = self.tree_file.selection()
        if not sel:
            return
        path = self.tree_file.item(sel[0], "values")[0]
        drive = path[:2]  # e.g. 'C:'
        rel = path[3:]    # 去掉 'C:\'
        try:
            self.core.commit_file_deletion(drive, rel)
            messagebox.showinfo("成功",
                                f"已提交删除（穿透覆盖层）:\n{path}")
            self.refresh()
        except uwf_core.UWFError as e:
            messagebox.showerror("失败", str(e))

    def _open_selected_dir(self):
        sel = self.tree_file.selection()
        if not sel:
            return
        path = self.tree_file.item(sel[0], "values")[0]
        try:
            os.startfile(os.path.dirname(path))
        except Exception:
            pass


def main():
    # 无界面自检模式：用于验证打包后的 exe 是否能正常访问 UWF
    if "--check" in sys.argv:
        log_path = os.path.join(os.path.dirname(
            os.path.abspath(sys.argv[0])), "uwf_check.log")
        lines = []
        c = uwf_core.UWFCore()
        try:
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
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        sys.exit(0 if lines[-1] == "RESULT=PASS" else 1)

    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = UWFApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
