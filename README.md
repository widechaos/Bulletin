# DangmuNews · 桌面弹幕新闻

<p align="center">
  <a href="https://github.com/widechaos/DangmuNews/releases/latest"><img src="https://img.shields.io/github/v/release/widechaos/DangmuNews?color=7AA2F7&label=release" alt="release"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-24283B" alt="platform">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB" alt="python">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/widechaos/DangmuNews?color=9ECE6A" alt="license"></a>
</p>

让新闻**以弹幕的形式直接飞在你的桌面上** —— 全透明、置顶、**鼠标穿透不挡任何程序操作**。只有当指针停在某条弹幕上时它才暂停并可点:**左键打开原文,右键 / 托盘开菜单**。

盯盘、追热点、当环境信息背景板都合适 —— 像财联社电报那样**连续滴出实时快讯**,而不是一股脑倒一堆旧闻。

> 基于 Python + Qt(PySide6),跨平台(macOS / Windows / Linux)。

---

## ✨ 特性

- **真·桌面弹幕,零闪烁** —— 单张全屏透明画布 + QPainter 一帧绘制;泳道 + 排队算法保证弹幕**永不重叠**,满了自动排队,天然控密度。
- **鼠标穿透** —— macOS 用原生 `NSWindow.ignoresMouseEvents`(不是不可靠的 Qt 透传);指针压上去才接管,移开立即恢复穿透,不会有「隐形墙」。
- **实时新闻流** —— RSS / RSSHub 秒级源;持久去重(重启不重放旧闻),启动只放最新几条打底,之后只放新条。**出现节奏按积压量铺满整个轮询周期**,连续自然不喷泉。
- **自建 RSSHub 中转支持** —— 公共实例常被墙、且无法从境外抓国内站;填上你自建的 RSSHub 基址即可接入财联社/华尔街见闻/微博/知乎等(自签证书 + basic-auth 均支持)。
- **B站式设置面板** —— 分区卡片 + 拨动开关 + 全滑块统一;**弹幕区域可视指引图**(拖动实时看满屏/半屏);**6 套经典主题可切换**(Tokyo Night / Dracula / Gruvbox / Solarized / One Dark / Catppuccin)。
- **精细过滤** —— 按来源逐个开关、关键词白名单 / 屏蔽词、彩色弹幕、描边加粗、长弹幕自适应加速。
- **多屏友好** —— 限定主屏 / 指定屏 / 横贯全部,弹幕不越界乱飞到外接显示器。
- **交互** —— 左键开原文、中键复制标题、右键菜单、悬停暂停;系统托盘控制;**启动自动检查更新**。

---

## 📦 安装

### 下载现成版本(推荐)

到 [**Releases**](https://github.com/widechaos/DangmuNews/releases/latest) 下载对应平台:

| 平台 | 文件 | 说明 |
|------|------|------|
| macOS | `DangmuNews-macOS.zip` | 解压得 `DangmuNews.app`,拖进「应用程序」。首次打开若拦截:右键→打开。菜单栏 agent,无 Dock 图标。 |
| Windows | `DangmuNews-Windows.zip` | 解压运行 `DangmuNews.exe`。 |
| Linux | `DangmuNews-Linux.tar.gz` | 解压运行 `DangmuNews/DangmuNews`。 |

### 从源码运行

```bash
git clone https://github.com/widechaos/DangmuNews.git
cd DangmuNews
pip install -r requirements.txt
python main.py
```

---

## ⚙️ 配置

首次运行生成配置文件:

- 源码运行 → 程序目录下 `config.json`
- 打包版 → 用户目录(macOS `~/Library/Application Support/DangmuNews/`,Windows `%APPDATA%\DangmuNews\`,Linux `~/.config/DangmuNews/`)

大部分设置在**托盘 → 设置**里图形化调整。要接入实时国内源,在设置里填 **RSSHub 基址**(你自建的实例),feeds 里的 `{rsshub}` 会自动替换成它。内置「实时·RSSHub」预设涵盖财联社电报、华尔街见闻快讯、微博热搜、知乎日报。

> ⚠️ `config.json` 可能含私有中转地址 / 凭据,已在 `.gitignore` 中,不会进仓库。

---

## 🛠 自行打包

```bash
pip install -r requirements.txt pyinstaller
pyinstaller DangmuNews.spec --noconfirm
# 产物在 dist/ —— mac 为 DangmuNews.app,win/linux 为 DangmuNews/ 目录
```

跨平台发布由 GitHub Actions 完成:推一个 `v*` tag(如 `git tag v0.1.0 && git push origin v0.1.0`)即自动为三大平台构建并发布到 Releases。

---

## 📄 License

[MIT](LICENSE)
