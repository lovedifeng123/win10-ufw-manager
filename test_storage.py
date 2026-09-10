"""磁盘检测功能只读测试（不修改任何系统设置）。

验证 uwf_core.list_storage() / find_overlay_files() 能正确枚举本机磁盘：
盘符、卷标、型号、介质类型（含傲腾识别）、总线、容量、可用空间，
以及各盘根目录已有 uwfswap.sys 的位置与大小。

运行： python test_storage.py
"""
import os
import sys

import uwf_core


def main():
    print("=== find_overlay_files(): 各盘根目录的 uwfswap.sys ===")
    found = uwf_core.find_overlay_files()
    if not found:
        print("  (未发现)")
    for k, v in sorted(found.items()):
        print(f"  {k}\\{uwf_core.OVERLAY_FILE_NAME}  {v:,.0f} MB")

    print()
    print("=== list_storage(): 固定磁盘卷 + 物理磁盘信息 ===")
    rows = uwf_core.list_storage()
    if not rows:
        print("  !! 未检测到任何卷 —— 检测逻辑失败")
        return 1
    hdr = ("盘符", "卷标", "型号", "类型", "总线", "总GB", "可用GB",
           "傲腾", "覆盖文件MB")
    print("  " + " | ".join(hdr))
    print("  " + "-" * 100)
    for r in rows:
        print("  " + " | ".join([
            r["letter"], r["label"] or "—", (r["model"] or "未知")[:32],
            r["media"], r["bus"], f"{r['size_gb']:.1f}",
            f"{r['free_gb']:.1f}", "是" if r["is_optane"] else "否",
            f"{r['swap_mb']:,.0f}" if r["swap_mb"] else "—",
        ]))

    print()
    print("=== 断言 ===")
    letters = {r["letter"] for r in rows}
    ok = True

    if "C:" not in letters:
        print("  [FAIL] 未检测到 C:")
        ok = False
    else:
        print("  [OK] 检测到 C:")

    optane = [r for r in rows if r["is_optane"]]
    if optane:
        print(f"  [OK] 傲腾盘识别: {[r['letter'] + ' ' + r['model'] for r in optane]}")
    else:
        print("  [WARN] 未识别出傲腾盘（本机应有一个 Intel Optane 卷）")

    with_swap = [r for r in rows if r["swap_mb"]]
    if with_swap:
        for r in with_swap:
            print(f"  [OK] 覆盖文件定位: {r['letter']} "
                  f"{r['swap_mb']:,.0f} MB（可用 {r['free_gb']:.1f} GB）")
    else:
        print("  [INFO] 当前没有任何盘存在 uwfswap.sys")

    # 型号/介质必须非空才有意义
    blank = [r["letter"] for r in rows if not r["model"]]
    if blank:
        print(f"  [WARN] 以下盘未取到物理磁盘型号: {blank}")
    else:
        print("  [OK] 所有盘都取到了物理磁盘型号")

    print()
    print("结果：" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
