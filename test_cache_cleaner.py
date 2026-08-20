"""
cache_cleaner.py 单元测试（无需 UWF / 不碰真实 C 盘）。

用临时目录模拟清理目标，验证：
  * 扫描能正确计算大小与文件数
  * 清理能正确删除文件并统计释放字节
  * 进度回调从 0 单调到 100
  * 取消逻辑生效
  * _collect_commit_targets 收集正确
"""
import os
import sys
import tempfile
import shutil
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cache_cleaner as cc


def make_fake_targets(root):
    """构造指向临时目录的 TARGETS 列表（结构类似真实 29 项）。"""
    a = os.path.join(root, "A")
    b = os.path.join(root, "B")
    os.makedirs(a, exist_ok=True)
    os.makedirs(b, exist_ok=True)
    # A 下 3 个文件，大小 100/200/300
    for i, sz in enumerate((100, 200, 300), 1):
        with open(os.path.join(a, f"f{i}.tmp"), "wb") as f:
            f.write(b"x" * sz)
    # B 下 2 个文件，大小 50/150
    for i, sz in enumerate((50, 150), 1):
        with open(os.path.join(b, f"g{i}.tmp"), "wb") as f:
            f.write(b"x" * sz)
    return [
        ("📁 临时A", [os.path.join(a, "*")], "descA", True),
        ("📁 临时B", [os.path.join(b, "*")], "descB", True),
    ]


def test_scan():
    root = tempfile.mkdtemp()
    try:
        targets = make_fake_targets(root)
        prog = []
        res = cc.scan_targets(targets, progress_cb=lambda p, m: prog.append(p))
        results = res["results"]
        assert len(results) == 2
        assert results[0]["size"] == 600, results[0]["size"]
        assert results[1]["size"] == 200, results[1]["size"]
        assert results[0]["count"] == 3 and results[1]["count"] == 2
        # 进度单调非减且收尾 100
        assert prog and prog[-1] == 100, prog
        assert all(prog[i] <= prog[i + 1] for i in range(len(prog) - 1)), prog
        print("PASS test_scan (size=600/200, progress ends 100, monotonic)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_clean():
    root = tempfile.mkdtemp()
    try:
        targets = make_fake_targets(root)
        res = cc.scan_targets(targets)
        selected = res["results"]
        prog = []
        summary = cc.clean_targets(
            selected, progress_cb=lambda p, m: prog.append(p),
            do_commit=False, do_exclude=False)
        # 文件应被删除
        assert not any(os.path.exists(os.path.join(root, d, fn))
                       for d in ("A", "B") for fn in os.listdir(os.path.join(root, d))) \
            or sum(len(os.listdir(os.path.join(root, d))) for d in ("A", "B")) == 0
        # 子目录可能仍存在（空），但文件数应为 0
        total_files = sum(len([f for f in os.listdir(os.path.join(root, d))
                               if os.path.isfile(os.path.join(root, d, f))])
                          for d in ("A", "B"))
        assert total_files == 0, total_files
        assert summary["total_freed"] == 800, summary["total_freed"]
        assert prog and prog[-1] == 100, prog
        assert all(prog[i] <= prog[i + 1] for i in range(len(prog) - 1)), prog
        assert any("临时A" in d for d in summary["details"])
        print("PASS test_clean (freed=800, progress 0->100, files removed)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cancel():
    root = tempfile.mkdtemp()
    try:
        targets = make_fake_targets(root)
        res = cc.scan_targets(targets)
        ev = threading.Event()
        ev.set()  # 一开始就取消
        summary = cc.clean_targets(
            res["results"], cancel_event=ev,
            do_commit=False, do_exclude=False)
        assert summary["cancelled"] is True
        # 取消后不应删除任何文件
        total_files = sum(len([f for f in os.listdir(os.path.join(root, d))
                               if os.path.isfile(os.path.join(root, d, f))])
                          for d in ("A", "B"))
        assert total_files == 5, total_files
        print("PASS test_cancel (cancelled=True, nothing deleted)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_collect_commit_targets():
    dirs, files = cc._collect_commit_targets([r"C:\Windows\Temp\*",
                                              r"C:\Windows\MEMORY.DMP"])
    assert dirs == {r"C:\Windows\Temp"}, dirs
    assert files == [r"C:\Windows\MEMORY.DMP"], files
    # 中间带 * 的文件通配 → 提交父目录
    dirs2, _ = cc._collect_commit_targets([r"C:\Windows\Prefetch\*.pf"])
    assert dirs2 == {r"C:\Windows\Prefetch"}, dirs2
    print("PASS test_collect_commit_targets (dir-wild→dir, file→file)")


if __name__ == "__main__":
    test_scan()
    test_clean()
    test_cancel()
    test_collect_commit_targets()
    print("\nALL CACHE_CLEANER TESTS PASSED")
