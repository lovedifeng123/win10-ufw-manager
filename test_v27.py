"""
UWF Manager Pro v2.7 深度测试（真实 UWF 环境）
运行环境：本机 UWF 已启用、C: 受保护。
原则：每一项可写操作都做 前/后 比对，并在 finally 中自还原，
      全部结束后断言与初始快照一致，绝不把机器留在变更状态。
"""
import sys
import time

import uwf_core

results = []
failed = []


def check(name, cond, detail=""):
    ok = bool(cond)
    results.append(ok)
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f"  | {detail}"
    print(line)
    if not ok:
        failed.append(name)


def snapshot(c):
    flt = c.get_filter()
    vols = c.get_volumes()
    ov = c.get_overlay() or {}
    file_exc = sorted(f"{e['drive']}{e['path']}" for e in c.get_exclusions())
    reg_exc = sorted(c.get_registry_exclusions())
    svc = c.get_servicing() or {}
    return {
        "filter_cur": flt.get("CurrentEnabled"),
        "filter_next": flt.get("NextEnabled"),
        "vol_next": {v["DriveLetter"]: bool(v.get("NextProtected")) for v in vols},
        "warn": ov.get("WarningOverlayThreshold"),
        "crit": ov.get("CriticalOverlayThreshold"),
        "file_exc": file_exc,
        "reg_exc": reg_exc,
        "svc_next": svc.get("NextEnabled"),
    }


def ensure_filter_next(c, target):
    flt = c.get_filter()
    if bool(flt.get("NextEnabled")) != bool(target):
        if target:
            c.enable_filter()
        else:
            c.disable_filter()


def ensure_volume_next(c, drive, target):
    for v in c.get_volumes():
        if v["DriveLetter"].upper() == drive.upper():
            cur = v.get("NextProtected")
            if cur is None:
                return
            if bool(cur) != bool(target):
                if target:
                    c.protect_volume(drive)
                else:
                    c.unprotect_volume(drive)
            return


def main():
    print("=" * 60)
    print(f"UWF Core v{uwf_core.UWF_CORE_VERSION} 深度测试")
    print("=" * 60)
    c = uwf_core.UWFCore()
    c.connect()
    init = snapshot(c)
    print("INIT:", init)
    print("-" * 60)

    # ---------- 读取 ----------
    check("读取 过滤器状态", init["filter_cur"] is not None)
    check("读取 卷(C:已保护)", init["vol_next"].get("C:") is True,
          str(init["vol_next"]))
    check("读取 覆盖阈值", init["warn"] is not None and init["crit"] is not None,
          f"warn={init['warn']} crit={init['crit']}")
    check("读取 文件排除(非空)", len(init["file_exc"]) > 0,
          f"{len(init['file_exc'])} 条")

    # ---------- 注册表排除（可逆）----------
    rk = r"HKLM\SOFTWARE\UWFPro27TestKey"
    before = set(c.get_registry_exclusions())
    try:
        c.add_registry_exclusion(rk)
        after_add = set(c.get_registry_exclusions())
        check("注册表排除·添加生效", rk in after_add, str(after_add))
    finally:
        try:
            c.remove_registry_exclusion(rk)
        except Exception:
            pass
    after_del = set(c.get_registry_exclusions())
    check("注册表排除·删除还原", after_del == before,
          f"before={before} after={after_del}")

    # ---------- 文件排除（可逆）----------
    fd, fr = "C:", r"\UWFPro27TestExclusion"
    ff = "C:\\UWFPro27TestExclusion"
    before_fe = set(snapshot(c)["file_exc"])
    try:
        c.add_exclusion(fd, fr)
        mid = set(snapshot(c)["file_exc"])
        check("文件排除·添加生效", ff in mid, ff)
    finally:
        try:
            c.remove_exclusion(fd, fr)
        except Exception:
            pass
    after_fe = set(snapshot(c)["file_exc"])
    check("文件排除·删除还原", after_fe == before_fe)

    # ---------- 阈值（可逆）----------
    warn0, crit0 = init["warn"], init["crit"]
    try:
        c.set_warning_threshold(1500)
        c.set_critical_threshold(3000)
        ov2 = c.get_overlay()
        check("警告阈值·修改生效", ov2.get("WarningOverlayThreshold") == 1500,
              str(ov2.get("WarningOverlayThreshold")))
        check("严重阈值·修改生效", ov2.get("CriticalOverlayThreshold") == 3000)
    finally:
        c.set_warning_threshold(warn0)
        c.set_critical_threshold(crit0)
    ov3 = c.get_overlay()
    check("阈值·已还原",
          ov3.get("WarningOverlayThreshold") == warn0 and
          ov3.get("CriticalOverlayThreshold") == crit0)

    # ---------- 服务模式（可逆）----------
    svc0 = init["svc_next"]
    try:
        c.set_servicing(True)
        svc_true = c.get_servicing().get("NextEnabled")
        check("服务模式·启用生效", svc_true is True, str(svc_true))
    finally:
        c.set_servicing(False)
    svc_final = c.get_servicing().get("NextEnabled")
    check("服务模式·已还原", svc_final == svc0,
          f"final={svc_final} orig={svc0}")

    # ---------- 覆盖层文件（只读）----------
    files = c.get_overlay_files("C:")
    check("覆盖层文件·读取无异常", isinstance(files, list),
          f"{len(files)} 个文件")

    # ---------- 主状态：过滤器开关（自还原）----------
    try:
        ensure_filter_next(c, False)
        check("过滤器·下次禁用生效",
              c.get_filter().get("NextEnabled") is False)
    finally:
        ensure_filter_next(c, init["filter_next"])
    check("过滤器·下次状态已还原",
          c.get_filter().get("NextEnabled") == init["filter_next"])

    # ---------- 主状态：卷保护（自还原）----------
    try:
        ensure_volume_next(c, "C:", False)
        vols_un = {v["DriveLetter"]: v.get("NextProtected")
                   for v in c.get_volumes()}
        check("C:·下次取消保护生效", vols_un.get("C:") is False)
    finally:
        ensure_volume_next(c, "C:", init["vol_next"].get("C:"))
    vols_fin = {v["DriveLetter"]: v.get("NextProtected")
                for v in c.get_volumes()}
    check("C:·下次保护已还原",
          vols_fin.get("C:") == init["vol_next"].get("C:"))

    # ---------- 最终一致性 ----------
    final = snapshot(c)
    consistent = (
        final["filter_next"] == init["filter_next"]
        and final["vol_next"] == init["vol_next"]
        and final["warn"] == warn0
        and final["crit"] == crit0
        and final["file_exc"] == init["file_exc"]
        and final["reg_exc"] == init["reg_exc"]
        and final["svc_next"] == init["svc_next"]
    )
    check("最终状态与初始快照完全一致", consistent,
          f"init={init}\nfinal={final}")

    print("-" * 60)
    print(f"总计 {len(results)} 项，通过 {sum(results)}，失败 {len(failed)}")
    if failed:
        print("失败项:", failed)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
