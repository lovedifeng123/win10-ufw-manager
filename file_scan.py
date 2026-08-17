"""
UWF Manager Pro - 文件扫描 / 覆盖缓存占用分析模块
UWF 不直接暴露"覆盖层里有哪些文件"的接口，本模块通过扫描受保护卷上
最近被修改 / 体积较大的文件来推断覆盖层的占用来源，帮助用户定位
"内存去哪了"。

优化策略：
  - 只扫描热点目录（Users / ProgramData / Temp / Program Files 等）
  - 多线程并行扫描，单目录超时保护
  - 避免全盘递归（C 盘数百万文件会使遍历超时）
"""
import os
import time
import threading

# 默认扫描的热点根目录（相对卷根）
DEFAULT_HOT_DIRS = [
    "Users",
    "ProgramData",
    "Program Files",
    "Program Files (x86)",
    "Windows\\Temp",
    "Windows\\SoftwareDistribution",
    "Temp",
]

# 需要跳过的已知超大/无关目录，减少无效遍历
SKIP_DIRS = {
    "node_modules", ".git", "WinSxS", "assembly", "CatRoot2",
    "$Recycle.Bin", "System32", "SysWOW64", "Microsoft.NET",
}


def _scan_dir(root, results, min_bytes, cutoff, deadline, limit, depth=0):
    """递归扫描单个目录（带超时与深度限制）。"""
    if time.time() > deadline or len(results) >= limit:
        return
    try:
        with os.scandir(root) as it:
            entries = list(it)
    except (PermissionError, OSError):
        return

    for e in entries:
        if time.time() > deadline or len(results) >= limit:
            return
        try:
            name = e.name
            if e.is_dir(follow_symlinks=False):
                if name in SKIP_DIRS:
                    continue
                if depth < 4:  # 限制递归深度
                    _scan_dir(e.path, results, min_bytes, cutoff,
                              deadline, limit, depth + 1)
            elif e.is_file(follow_symlinks=False):
                st = e.stat(follow_symlinks=False)
                size = st.st_size
                mtime = st.st_mtime
                if size >= min_bytes and mtime >= cutoff:
                    results.append({
                        "path": e.path,
                        "size_bytes": size,
                        "mtime": mtime,
                        "ext": os.path.splitext(name)[1].lower(),
                    })
        except (PermissionError, OSError):
            continue


def scan_volume(drive_letter, top_n=500, min_size_mb=10, days=30,
                hot_dirs=None, timeout_sec=20):
    """
    扫描指定卷上最近修改 / 较大的文件。
    :param drive_letter: 如 'C:'
    :param top_n: 最多返回数量
    :param min_size_mb: 最小文件大小(MB)
    :param days: 只统计最近 N 天内有修改的文件
    :param hot_dirs: 扫描的热点子目录列表；None 用默认
    :param timeout_sec: 整个扫描的超时（秒）
    :return: list of dict（已按大小降序截断到 top_n）
    """
    if hot_dirs is None:
        hot_dirs = DEFAULT_HOT_DIRS
    root = f"{drive_letter}\\"
    now = time.time()
    cutoff = now - days * 86400
    min_bytes = min_size_mb * 1024 * 1024
    deadline = now + timeout_sec

    results = []
    limit = top_n * 2
    threads = []

    scan_roots = [os.path.join(root, d) for d in hot_dirs
                  if os.path.isdir(os.path.join(root, d))]

    def worker(sr):
        _scan_dir(sr, results, min_bytes, cutoff, deadline, limit)

    for sr in scan_roots:
        t = threading.Thread(target=worker, args=(sr,), daemon=True)
        t.start()
        threads.append(t)

    # 等待所有线程完成或超时
    for t in threads:
        remaining = deadline - time.time()
        if remaining > 0:
            t.join(timeout=remaining)
        else:
            break

    results.sort(key=lambda x: x["size_bytes"], reverse=True)
    return results[:top_n]


def format_size(n):
    """字节转人类可读字符串。"""
    if n is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == "TB":
            return f"{f:.2f} {u}"
        f /= 1024
    return f"{n} B"


def ext_summary(files):
    """按扩展名聚合大小。"""
    agg = {}
    for f in files:
        ext = f["ext"] or "(无扩展名)"
        agg[ext] = agg.get(ext, 0) + f["size_bytes"]
    return sorted(agg.items(), key=lambda x: x[1], reverse=True)


if __name__ == "__main__":
    t0 = time.time()
    r = scan_volume("C:", top_n=15, min_size_mb=50, days=7, timeout_sec=15)
    print(f"scan took {time.time()-t0:.1f}s, found {len(r)}")
    for x in r:
        print(" ", format_size(x["size_bytes"]), x["ext"], x["path"][:60])
