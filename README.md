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
- 🚫 **排除列表 / Exclusions** — 查看与管理系统受保护卷的排除项，文件排除与**注册表排除**分开管理（独立选项卡，支持导入/导出/右键删除），直接写入真实磁盘，与开源 [UWFPRO](https://github.com/FrenzyPig/UWFPRO) 对齐。
- 🛠 **服务模式 / Servicing mode** — 启用「服务模式」便于系统更新时穿透覆盖层（下次重启生效）。
- 🔧 **操作 / Actions** — 启用 / 禁用 UWF、提交删除（穿透覆盖层写入真实磁盘）、重启。
- 🔍 **托盘常驻 / System tray** — 最小化到托盘，后台持续运行与监控。

---

## 📌 更新日志 / Changelog

### v2.7 (2026-08-18)
- ✨ **补齐 UWFPRO 缺失能力（深度测试通过）** — 在真实已启用 UWF 的环境完成 19 项深度测试，全部通过且自还原，机器状态与初始快照完全一致。
  - 📁 **注册表排除 / Registry exclusions** — 新增「注册表排除」独立选项卡：添加 / 删除 / 提交注册表值 / 导入导出 / 右键删除，与 UWFPRO 功能对齐。
  - 🛠 **服务模式 / Servicing** — 新增服务模式启用/禁用（设置面板）。注意：本机实测 `uwfmgr.exe servicing` 子命令在部分 Windows 版本返回「不支持」（0x85E00005），但 WMI `UWF_Servicing.Enable/Disable` 实际可用；故内部改走 WMI 并做二次状态校验，确保可用。
  - 📂 **覆盖层文件只读列表** — 文件分析页新增「覆盖层文件」只读列表（走 WMI `UWF_Overlay.GetOverlayFiles`，因该方法在部分环境会挂起，已加 15 秒线程超时容错，超时返回空列表不影响主线程）。
  - 🧹 **排除项去重** — `get_exclusions` 按 (盘符, 路径) 去重，避免 UWF_Volume 双会话实例导致的重复计数。
- 🐞 **修复写入路径致命缺陷** — 补全此前遗漏的 `UWFError` / `UWFNotSupported` 异常类定义（否则任意写操作都会 `NameError` 崩溃）。

### v2.6 (2026-08-18)
- 🐛 **修复启动即崩（关键）** — 修复 `Treeview.column("会话")` / `column("生效")` 的 `TclError: Invalid column index`（列定义与 column ID 不一致导致一打开就崩溃）。
- ✅ **无需管理员启动** — 改回 `asInvoker`（启动不弹 UAC）；写操作通过 `ShellExecuteEx(runas)` **按需自动提权**（仅在该操作时弹一次 UAC），和参考软件 `UWFPRO` 行为一致：普通用户可打开浏览，写操作时才请求授权。
- 🧹 **清除所有 admin 门卫** — 移除全部 11 处 `if not self.admin:` 拦截（开启/关闭保护、关机保护、提交删除、排除项、缓存设置、分区保护等），所有操作均走按需提权。

### v2.5 (2026-08-18)
- 🔔 **托盘显示剩余内存** — 右下角托盘 tooltip 实时显示「UWF 状态 + 剩余内存 X MB（已用 Y MB）」，不再只显示一个盾牌图标。
- 🛡 **内嵌管理员清单（关键修复）** — 打包时嵌入 `requireAdministrator` 清单，软件启动即自动提权（与参考软件 `UWF管理器.exe` 行为一致）。此前的 `asInvoker` 导致 `uwfmgr` 写入静默失败，正是“分区保护/开启保护根本无效”的根因。
- 🗂 **分区保护待生效态修正** — 正确解析 `UWF_Volume` 的“当前会话/下次会话”双实例，分区保护表新增「重启后状态」列，保护/取消保护后明确显示「已保护（重启生效）」/「未保护（重启取消）」，重启后自动消除待生效标记；并新增「立即重启生效」按钮。

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
- **普通用户即可启动浏览**；写操作（保护/排除/设置变更等）会**自动弹出 UAC 请求管理员授权**。
- UWF 依赖特定过滤驱动，通常与 Hyper-V 等存在共存限制，请确认环境支持。
- **English**: Windows 10/11 **Enterprise / Education / IoT Enterprise** with the UWF feature enabled; **launch as normal user** — write operations auto-elevate via UAC on demand.

---

## 🚀 快速使用 / Quick Start

1. 前往 [Releases](../../releases) 下载 `UWF Manager Pro.exe`。
2. **双击直接运行**（无需右键管理员）。
3. 首次打开会自动检测 UWF 状态并加载数据；执行写操作时会自动弹出 UAC 授权。

---

## 🛠️ 从源码构建 / Build from Source

```bash
pip install -r requirements.txt

pyinstaller "UWF Manager Pro.spec"

# 或手动指定（无需 --uac-admin，启动不弹 UAC，写操作按需提权）：
pyinstaller --onefile --windowed --name "UWF Manager Pro" ^
  --add-data "uwf_core.py;." ^
  --add-data "file_scan.py;." ^
  --add-data "overlay_monitor.py;." ^
  main.py
```

生成的单文件 exe 位于 `dist/UWF Manager Pro.exe`（`asInvoker` 清单，普通用户可启动；写操作自动 UAC 提权）。

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
