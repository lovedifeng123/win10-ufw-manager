"""历史功能核对回归测试（防止"修一个丢一个"）。

核对 GitHub 上历次发布版本引入的能力在最新版中仍然存在，
并实测托盘动态图标是否真能渲染出数字（v2.20~v2.22 曾因打包漏装 Pillow 而失效）。

用法：python test_features.py
"""
import importlib

import uwf_core
import main

CORE_METHODS = [
    ("enable_filter", "v2.x 过滤器开关"),
    ("disable_filter", "v2.x 过滤器开关"),
    ("protect_volume", "v2.12 卷保护"),
    ("unprotect_volume", "v2.12 取消卷保护"),
    ("set_overlay_type", "v2.x 覆盖类型(内存/磁盘)"),
    ("set_maximum_size", "v2.x 最大缓存"),
    ("set_warning_threshold", "v2.x 警告阈值"),
    ("set_critical_threshold", "v2.x 严重阈值"),
    ("create_swapfile", "v2.20 选盘建覆盖文件"),
    ("batch_commit", "v2.18 批量提交删除"),
    ("batch_exclude", "v2.18 批量排除"),
    ("get_servicing", "v2.7 服务模式"),
    ("get_volumes", "v2.x 受保护卷"),
    ("get_overlay", "v2.x 运行态覆盖层"),
    ("get_overlay_config", "v2.15 覆盖层配置(修 MaximumSize 读错)"),
    ("get_overlay_files", "v2.7 覆盖层文件"),
    ("list_storage", "v2.20 磁盘枚举/傲腾识别"),
    ("find_overlay_files", "v2.20 覆盖文件定位"),
    ("uwf_availability", "v2.22 新机可用性诊断"),
    ("enable_uwf_feature", "v2.22 一键启用UWF功能"),
]

APP_METHODS = [
    ("on_toggle", "v2.12 开启/关闭保护(卷+过滤器)"),
    ("on_clean_cache", "v2.16/2.18 清理缓存释放覆盖层"),
    ("on_commit_all", "v2.x 提交所有删除"),
    ("on_restart", "v2.19 重启(改用shutdown.exe)"),
    ("on_shutdown", "v2.19 关机"),
    ("on_protect_volume", "v2.12 保护指定卷"),
    ("on_unprotect_volume", "v2.12 取消保护"),
    ("_open_overlay_disk_dialog", "v2.20 选盘对话框"),
    ("on_enable_uwf_auto", "v2.10/2.22 一键开启UWF"),
    ("_update_tray", "v2.13~2.15 托盘动态百分比"),
    ("_check_overlay_threshold", "v2.8 覆盖层阈值气泡提醒"),
    ("_set_status_text", "v2.23 状态文字(待重启生效)"),
    ("_update_overlay_mode_label", "v2.23 覆盖模式显示"),
    ("_show_uwf_guide", "v2.22 新机开启引导"),
]

print("=" * 72)
print("UWF Manager Pro 历史功能核对")
print("=" * 72)

fails = []

print("\n[1] uwf_core 能力")
for name, desc in CORE_METHODS:
    ok = hasattr(uwf_core.UWFCore, name) or hasattr(uwf_core, name)
    print(f"    {'OK  ' if ok else 'MISS'} {name:26s} {desc}")
    if not ok:
        fails.append(f"uwf_core.{name}")

print("\n[2] UWFApp 能力")
for name, desc in APP_METHODS:
    ok = hasattr(main.UWFApp, name)
    print(f"    {'OK  ' if ok else 'MISS'} {name:28s} {desc}")
    if not ok:
        fails.append(f"UWFApp.{name}")

TRAY_METHODS = [
    ("show_balloon", "v2.8 托盘气泡通知"),
    ("update_icon", "v2.13~2.15 托盘动态百分比图标"),
    ("update_tooltip", "v2.x 托盘提示文字"),
]

print("\n[2b] SystemTrayIcon 能力")
for name, desc in TRAY_METHODS:
    ok = hasattr(main.SystemTrayIcon, name)
    print(f"    {'OK  ' if ok else 'MISS'} {name:20s} {desc}")
    if not ok:
        fails.append(f"SystemTrayIcon.{name}")

print("\n[3] 模块依赖")
try:
    importlib.import_module("PIL")
    from PIL import Image, ImageDraw, ImageFont
    print("    OK   Pillow（托盘动态图标依赖，v2.20~v2.22 曾漏装导致数字消失）")
except Exception as e:
    print("    MISS Pillow ->", e)
    fails.append("Pillow")

for m in ("win32gui", "win32api", "win32con", "win32com.client", "tkinter"):
    try:
        importlib.import_module(m)
        print(f"    OK   {m}")
    except Exception as e:
        print(f"    MISS {m} -> {e}")
        fails.append(m)

print("\n[4] 托盘动态图标实测（渲染数字 '17'）")
try:
    tray = main.SystemTrayIcon(None)
    hicon = tray._create_hicon_from_text("17")
    ok = bool(hicon) and int(hicon) != 0
    print(f"    hicon = {hicon}  -> {'渲染成功 ✅' if ok else '渲染失败 ❌'}")
    if not ok:
        fails.append("tray icon render")
    # 再测警告色与不同长度文本
    for t in ("100", "OFF", "3"):
        h2 = tray._create_hicon_from_text(t, warn=(t == "3"))
        if not (h2 and int(h2) != 0):
            print(f"    渲染 {t} 失败")
            fails.append(f"tray icon '{t}'")
    try:
        tray.destroy()
    except Exception:
        pass
except Exception as e:
    print("    托盘图标测试异常:", e)
    fails.append("tray icon exception")

print("\n" + "=" * 72)
if fails:
    print("结果：存在缺失 ❌ ->", ", ".join(fails))
else:
    print("结果：历史功能全部在位 ✅")
print("=" * 72)
