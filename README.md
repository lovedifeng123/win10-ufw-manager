# Win10 自带还原 UWF · UWF Manager Pro
# Win10 Built-in Restore UWF · UWF Manager Pro

> 🤖 **本项目由用户与 AI 协作开发**，目前为**初步版本（Preliminary）**，功能会持续迭代更新。
> This project is **co-developed by a user and an AI assistant**. It is currently a **preliminary version** and will be improved over time.

[English version below / 中文说明在上]

---

## 简介 / Introduction

Windows 10/11 企业版、教育版、IoT 企业版自带 **UWF（Unified Write Filter，统一写入筛选器）**。它会把对所有受保护卷（通常是 C 盘）的“写入”重定向到一个**覆盖层（Overlay）**——可以是内存，也可以是磁盘。重启后覆盖层被丢弃，系统**一键还原**到原始状态，相当于系统自带的“影子系统 / 还原模式”。

Windows 10/11 **Enterprise / Education / IoT Enterprise** editions ship with **UWF (Unified Write Filter)**. It redirects every write to a protected volume (usually `C:`) into an **overlay** (in memory or on disk). After a reboot the overlay is discarded and the system is **restored** to its original state — essentially a built-in “shadow / stealth mode” for Windows.

但微软只提供了命令行 `uwfmgr.exe`，**没有图形界面**。本工具就是 UWF 的**图形化管理器**，让你像用普通软件一样查看、配置、监控 UWF。

Microsoft only ships the `uwfmgr.exe` command line — **no GUI**. This tool is a **graphical manager for UWF**, so you can view, configure, and monitor UWF like a normal desktop app.

---

## ✨ 功能特性 / Features

- 📊 **状态面板 / Status dashboard** — UWF 启用状态、覆盖层已用/剩余内存、阈值进度条（接近上限自动变红）。
- 💾 **文件浏览器 / File explorer** — 找出哪些文件、目录正在“吃掉”你的覆盖层内存。
- 📝 **实时写入监控 / Real-time write monitor** — 开启后持续监听系统盘写入，实时滚动显示“有哪些文件刚写进了内存”，可导出 TXT。
- ⚙️ **设置面板 / Settings** — 最大缓存、警告/严重阈值、写入过滤、覆盖类型、HORM、排除列表（UWF 启用时控件自动只读，避免无效修改）。
- 🚫 **排除列表 / Exclusions** — 查看与管理系统受保护卷的排除项（文件 / 注册表），直接写入真实磁盘。
- 🔧 **操作 / Actions** — 启用 / 禁用 UWF、提交删除（穿透覆盖层写入真实磁盘）、重启。
- 🔍 **托盘常驻 / System tray** — 最小化到托盘，后台持续运行与监控。

---

## 📌 更新日志 / Changelog

### v2.4 (2026-08-18)
- 🛠 **修复所有写入操作失效** — 启用 / 禁用保护、开启 / 关闭保护、关机保护、排除项增删、阈值设置等全部改为调用官方 `uwfmgr.exe`，彻底解决此前按钮无响应的问题。
- 🔔 **真实系统托盘** — 改用 Win32 原生通知区图标（右下角任务栏），支持左键双击恢复窗口、右键菜单（显示主窗口 / 退出）。
- ⚡ **覆盖层内存实时刷新** — 状态面板的内存水位每 3 秒自动刷新，无需手动点击。
- ⚠️ RAM 模式覆盖层上限为 1024 MB，超出会给出友好提示。

### v2.3 (2026-08-13)
- 初始公开版本（Initial public release）。

---

## 📋 系统要求 / Requirements

- Windows 10 / 11 **企业版 / 教育版 / IoT 企业版**（需支持 UWF 功能）。
- 必须以**管理员身份**运行。
- UWF 依赖特定过滤驱动，通常与 Hyper-V 等存在共存限制，请确认环境支持。
- **English**: Windows 10/11 **Enterprise / Education / IoT Enterprise** with the UWF feature enabled; must be run **as Administrator**.

---

## 🚀 快速使用 / Quick Start

1. 前往 [Releases](../../releases) 下载 `UWF Manager Pro.exe`。
2. **右键 → 以管理员身份运行**。
3. 首次打开会自动检测 UWF 状态并加载数据。

---

## 🛠️ 从源码构建 / Build from Source

```bash
pip install -r requirements.txt

pyinstaller --onefile --windowed --name "UWF Manager Pro" ^
  --add-data "uwf_core.py;." ^
  --add-data "file_scan.py;." ^
  --add-data "overlay_monitor.py;." ^
  main.py
```

生成的单文件 exe 位于 `dist/UWF Manager Pro.exe`。

> 说明：WMI 的 `GetOverlayFiles` 在部分环境枚举量过大时会挂起，故实时监控改用 `ReadDirectoryChangesW` 文件系统监听（UWF 下所有系统盘写入都落在覆盖层，等价于监控“写进了内存的文件”）。

---

## 📖 UWF 小知识 / About UWF

覆盖层有大小上限（默认或自定义）。一旦写入超出上限，系统可能蓝屏或强制重启。本工具帮你**盯着这条水位线**，并告诉你“内存被哪些文件吃掉了”。

The overlay has a size limit. Exceeding it can crash or force-reboot the system. This tool helps you **watch that water level** and shows you **which files are eating your overlay memory**.

---

## 🤝 关于本项目 / About This Project

- 🤖 **AI 与用户协作 / Built with AI** — 软件由用户提出需求、亲自测试与反馈，并与 AI 助手共同完成编码、调试与打包。
- 🐣 **初步版本 / Preliminary** — 当前为早期版本，核心功能已可用，后续会持续迭代：更多设置项、更友好的提示、可能的国际化等。
- 💡 仓库公开后，欢迎提交 Issue 与 Pull Request。

---

## ⚠️ 免责声明 / Disclaimer

本软件按“原样”提供，作者不对使用造成的任何系统问题负责。修改 UWF 配置、提交删除等操作会真实改变系统状态，请谨慎使用。

Provided “as is”, without warranty of any kind. Changing UWF settings or committing deletions **does** affect the real system state — use with care.

---

## 📄 许可证 / License

[MIT](LICENSE) — Copyright © 2026 UWF Manager Pro & AI collaborators.

---

## 🙏 鸣谢 / Credits

- 用户与 AI 协作：需求定义、测试验证、反馈迭代。
- AI 助手：架构设计、编码实现、调试与打包。
