# UWF Manager Pro

一个轻量、开源的 Windows Unified Write Filter (UWF) 管理工具。

> 原生 Python + tkinter 实现，无需 .NET / Qt 运行时，单文件 exe 即可运行。

## 功能

- **UWF 状态面板**：实时显示 UWF 已启用/禁用状态、关机待处理、HORM 状态。
- **覆盖层内存监控**：可视化显示覆盖层已用 / 总容量，进度条根据
  警告 / 临界阈值自动变色（蓝 → 橙 → 红）。
- **受保护卷列表**：列出每个卷的保护状态、会话信息。
- **文件浏览器**：扫描受保护卷上最近修改的大文件，帮你定位
  「覆盖层的内存都被哪些文件吃掉了」，原始路径一目了然。
- **覆盖层文件日志（内存文件清单）**：一键列出自「本次开机」以来被写入 /
  修改、实际暂存在 UWF 覆盖层（内存）中的文件——这些文件**原始位置都在
  受保护的 C: 盘，现在却占用内存**，重启会丢失（除非先「提交」）。
  显示文件总数、合计大小、每条的原始路径 / 大小 / 类型 / 状态，并支持
  **导出为 TXT** 自行核对与清理。
- **操作**：启用 / 禁用 UWF、提交所有删除、刷新、设置覆盖上限。

## 为什么不用原版 UWF.1.0.17.exe？

原版存在两个已知问题：
1. 读取 `UWF_Volume` 时强制对所有卷（含未保护卷）要求布尔类型，在部分
   环境下会抛出
   `UWF 状态不可用：decode WMI row, field 'Protected' has the wrong type` 而崩溃。
2. 读取了不存在的字段 `UWF_Overlay.UsedSpace`（恒为 NULL），导致状态面板
   无法正常显示。

本工具使用容错解析，字段名完全对齐微软官方 WMI 定义，并对所有 COM 访问
做了**跨线程隔离（每个后台线程独立 `CoInitialize` + 独立连接）**，彻底
避免了「打开后一直卡在检测中」的问题。

## 系统要求

- Windows 10/11 企业版 / 教育版 / IoT（支持 UWF 的系统）
- **必须以管理员身份运行**（UWF WMI 接口需要管理员权限）
- 无需安装任何运行时

## 使用

1. 右键 `UWF Manager Pro.exe` → 以管理员身份运行
2. 查看状态面板与覆盖内存使用情况
3. 切换到「文件浏览器」扫描最近修改的大文件，定位内存占用来源
4. 如需释放覆盖层，可「提交所有删除」或重启计算机

## 构建

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "UWF Manager Pro" main.py
```

产物位于 `dist/UWF Manager Pro.exe`。

## 技术说明

通过 `win32com` 访问 WMI 命名空间 `root\standardcimv2\embedded`：

| WMI 类 | 用途 |
|--------|------|
| `UWF_Filter` | 启用 / 禁用状态、HORM、关机待处理 |
| `UWF_Volume` | 各卷保护状态、提交待定 |
| `UWF_Overlay` | 覆盖层已用 / 可用 / 阈值 |
| `UWF_OverlayConfig` | 覆盖层类型与最大容量 |

## License

MIT
