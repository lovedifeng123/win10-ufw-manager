"""新机器场景深度测试（只读 / 不改系统）。

用法：
    python test_newmachine.py              # 正常场景：本机已装 UWF
    python test_newmachine.py --simulate   # 模拟新机：uwfmgr.exe 不存在

验证点：
  1. uwf_core.uwf_availability() 三种状态判定正确
  2. 「开启 UWF」引导卡片在功能未安装时**显示**（此前会被错误隐藏）
  3. 功能未安装时写操作按钮被禁用（不会点了才报"找不到 uwfmgr.exe"）
"""
import sys
import tkinter as tk

import uwf_core
import main

SIMULATE = "--simulate" in sys.argv

if SIMULATE:
    # 模拟新机器：让 uwfmgr.exe "不存在"
    uwf_core._UWF_EXE_CANDIDATES = (r"Z:\__nonexistent__\uwfmgr.exe",)

print("=" * 70)
print("场景：", "模拟新机（uwfmgr.exe 不存在）" if SIMULATE else "正常（本机已装 UWF）")
print("=" * 70)

# --- 1. 直接测 uwf_availability() ---
avail = uwf_core.uwf_availability()
print("\n[1] uwf_core.uwf_availability()")
print("    available :", avail["available"])
print("    supported :", avail["supported"])
print("    state     :", avail["state"])
print("    product   :", avail["product"])
print("    edition   :", avail["edition"] or "(空)")

ok1 = True
if SIMULATE:
    ok1 = (avail["available"] is False)
    print("    期望 available=False ->", "PASS" if ok1 else "FAIL")
else:
    ok1 = (avail["available"] is True and avail["state"] == "ok")
    print("    期望 available=True/state=ok ->", "PASS" if ok1 else "FAIL")

# --- 2/3. 真实构建 UI，验证引导卡片与按钮 ---
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass
try:
    import pythoncom
    pythoncom.CoInitialize()
except Exception:
    pass

root = tk.Tk()
app = main.UWFApp(root)
res = {}


def probe():
    a = getattr(app, "uwf_avail", {})
    res["state"] = a.get("state")
    res["status_text"] = app.lbl_status.cget("text")
    try:
        res["guide_visible"] = bool(app.guide_frame.winfo_ismapped())
    except Exception:
        res["guide_visible"] = None
    for n in ("btn_toggle", "btn_apply_basic", "btn_apply_cache"):
        try:
            res[n] = str(getattr(app, n).cget("state"))
        except Exception:
            res[n] = "N/A"
    root.destroy()


root.after(3500, probe)
root.mainloop()

print("\n[2] 状态标签 / 引导卡片 / 按钮")
print("    lbl_status        :", res.get("status_text"))
print("    引导卡片可见      :", res.get("guide_visible"))
print("    btn_toggle        :", res.get("btn_toggle"))
print("    btn_apply_basic   :", res.get("btn_apply_basic"))
print("    btn_apply_cache   :", res.get("btn_apply_cache"))

if SIMULATE:
    ok2 = (res.get("guide_visible") is True)
    ok3 = all(res.get(n) == "disabled"
              for n in ("btn_toggle", "btn_apply_basic", "btn_apply_cache"))
    print("\n    期望：引导可见=True ->", "PASS" if ok2 else "FAIL")
    print("    期望：写按钮全部 disabled ->", "PASS" if ok3 else "FAIL")
else:
    ok2 = (res.get("guide_visible") is False)
    ok3 = all(res.get(n) == "normal"
              for n in ("btn_toggle", "btn_apply_basic", "btn_apply_cache"))
    print("\n    期望：引导可见=False ->", "PASS" if ok2 else "FAIL")
    print("    期望：写按钮全部 normal ->", "PASS" if ok3 else "FAIL")

print("\n" + "=" * 70)
print("结果：", "全部通过 ✅" if (ok1 and ok2 and ok3) else "存在失败 ❌")
print("=" * 70)
