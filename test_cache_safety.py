"""
深度安全测试：清理功能绝不会误删用户资料 / D 盘 / E 盘 / 盘符根 / 用户主目录。

只读测试 —— 仅扫描并计算"本应删除"的文件集合，并逐一断言其位于预期缓存目录内。
绝不调用 clean_targets()，不删除任何文件。

运行：buildenv2/Scripts/python.exe test_cache_safety.py
"""
import os
import glob
import sys

import cache_cleaner as cc

DANGEROUS_ROOTS = [
    os.path.abspath("C:\\"),
    os.path.abspath("D:\\"),
    os.path.abspath("E:\\"),
    os.path.abspath(os.environ.get("USERPROFILE", "C:\\Users")),
    os.path.abspath(os.environ.get("SystemDrive", "C:") + "\\"),
]

ALLOWED_CACHE_PREFIXES = [
    os.path.abspath(os.environ.get("TEMP", r"C:\Windows\Temp")),
    r"C:\Windows\Temp",
    r"C:\Windows\SoftwareDistribution",
    os.path.abspath(os.path.join(os.environ.get("LOCALAPPDATA", ""), "")),
    r"C:\Windows\Prefetch",
]


def is_under_allowed(path):
    ap = os.path.abspath(path)
    for pre in ALLOWED_CACHE_PREFIXES:
        if ap.startswith(pre + os.sep) or ap == pre:
            return True
    return False


def main():
    print("=" * 70)
    print("深度安全测试：清理目标范围校验")
    print("=" * 70)
    failures = []
    warnings = []

    # 1) 每个目标的基准目录必须解析为明确、安全的 C: 缓存目录
    print("\n[1] 校验每个清理项的基准目录（_abs_base）")
    for label, patterns, desc, default in cc.TARGETS:
        for pat in patterns:
            base = cc._abs_base(pat)
            name = cc._name(label)
            if base is None:
                print(f"   [跳过/安全] {name}: 模式 {pat!r} 不安全 → 整项跳过")
                continue
            ab = os.path.abspath(base)
            drive = os.path.splitdrive(ab)[0].upper()
            if drive not in ("C:",):
                failures.append(f"{name}: 基准目录不在 C: 盘: {ab}")
            if ab in DANGEROUS_ROOTS:
                failures.append(f"{name}: 基准目录是危险根目录: {ab}")
            if ".." in os.path.normpath(ab).split(os.sep):
                failures.append(f"{name}: 基准目录含不安全路径: {ab}")
            print(f"   [OK] {name}: base={ab}")

    # 2) 真实扫描：收集"本应删除"的文件，逐一断言安全
    print("\n[2] 真实扫描（只读）并校验每个待删文件位于预期缓存目录内")
    total_bytes = 0
    total_files = 0
    affected_dirs = set()
    for label, patterns, desc, default in cc.TARGETS:
        for pat in patterns:
            base = cc._abs_base(pat)
            if base is None:
                continue
            expanded = os.path.expandvars(pat)
            for fpath in glob.glob(expanded):
                if not cc._safe_to_remove(fpath, base):
                    failures.append(f"待删文件未通过 _safe_to_remove: {fpath}")
                    continue
                af = os.path.abspath(fpath)
                drive = os.path.splitdrive(af)[0].upper()
                if drive in ("D:", "E:"):
                    failures.append(f"严重：待删文件位于数据盘 {drive}: {af}")
                if af in DANGEROUS_ROOTS:
                    failures.append(f"严重：待删文件是危险根/主目录: {af}")
                if not is_under_allowed(af):
                    failures.append(f"待删文件不在允许的缓存前缀内: {af}")
                affected_dirs.add(os.path.dirname(af))
                try:
                    if os.path.isfile(af):
                        total_bytes += os.path.getsize(af)
                        total_files += 1
                except OSError:
                    pass

    print(f"   可清理文件数(仅文件): {total_files}，估算大小: {cc._human(total_bytes)}")
    print(f"   涉及的顶级目录数: {len(affected_dirs)}")
    for d in sorted(affected_dirs)[:30]:
        print(f"     - {d}")

    # 3) 护栏：模拟 TEMP 为空，用户临时文件项必须被跳过（不退化成裸 *）
    print("\n[3] 护栏：TEMP 为空时，用户临时文件项应安全跳过")
    saved = os.environ.get("TEMP")
    os.environ["TEMP"] = ""
    try:
        temp_pat = os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "*")
        base_empty = cc._abs_base(temp_pat)
        # 注意：_abs_base 用 expandvars，TEMP="" 时 join("", "*")="\*"? 实际是 "*"
        # expandvars("*")="*" → 相对路径 → 返回 None（被拒绝）
        if base_empty is None:
            print("   [OK] TEMP 为空 → 用户临时文件项被跳过（不会退化成裸 '*' 误删启动目录）")
        else:
            # 回退到 C:\Windows\Temp 时 base 应为该安全目录
            print(f"   [INFO] TEMP 为空 → base={base_empty}（回退到安全绝对目录）")
            if not str(base_empty).upper().startswith("C:\\WINDOWS\\TEMP"):
                failures.append(f"TEMP 为空时回退目录不安全: {base_empty}")
    finally:
        if saved is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = saved

    # 4) _safe_to_remove 必须拒绝盘符根 / 用户主目录
    print("\n[4] _safe_to_remove 拒绝危险路径")
    for dangerous in [r"C:\\", r"D:\\", os.environ.get("USERPROFILE", ""), r"C:\\Windows"]:
        if cc._safe_to_remove(dangerous, r"C:\Windows\Temp"):
            failures.append(f"_safe_to_remove 错误放行危险路径: {dangerous}")
    if not cc._safe_to_remove(r"C:\Windows\Temp\foo.tmp", r"C:\Windows\Temp"):
        failures.append("_safe_to_remove 错误拒绝合法缓存文件")
    print("   [OK] 危险路径均被拒绝，合法缓存文件被接受")

    # 结果
    print("\n" + "=" * 70)
    if failures:
        print(f"❌ 测试失败 {len(failures)} 项：")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    else:
        print("✅ 全部通过：清理功能仅作用于 C: 纯缓存目录，绝不误删用户资料/D/E 盘/根目录")
        print("=" * 70)


if __name__ == "__main__":
    main()
