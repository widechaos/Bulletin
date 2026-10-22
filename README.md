# Bulletin · 弹讯

> 桌面弹幕新闻 —— 让实时快讯以弹幕飞在桌面上

<p align="center">
  <a href="https://github.com/widechaos/Bulletin/releases/latest"><img src="https://img.shields.io/github/v/release/widechaos/Bulletin?color=7AA2F7&label=release" alt="release"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-24283B" alt="platform">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB" alt="python">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-PolyForm%20Noncommercial-9ECE6A" alt="license"></a>
</p>

让新闻**以弹幕的形式直接飞在你的桌面上** —— 全透明、置顶、**鼠标穿透不挡任何程序操作**。只有当指针停在某条弹幕上时它才暂停并可点:**左键打开原文,右键 / 托盘开菜单**。

盯盘、追热点、当环境信息背景板都合适 —— 新条目按节奏**连续放出**,不会一下倒出一堆旧闻。

> 基于 Python + Qt(PySide6),跨平台(macOS / Windows / Linux)。

---

## ✨ 特性

- **真·桌面弹幕,零闪烁** —— 单张全屏透明画布 + QPainter 一帧绘制;泳道 + 排队算法保证弹幕**永不重叠**,满了自动排队,天然控密度。
- **鼠标穿透** —— macOS 用原生 `NSWindow.ignoresMouseEvents`(不是不可靠的 Qt 透传);指针压上去才接管,移开立即恢复穿透,不会有「隐形墙」。
- **实时新闻流** —— RSS / RSSHub 源,按设定间隔检查更新;持久去重(重启不重放旧闻),启动只放最新几条打底,之后只放新条。**出现节奏按积压量铺满整个轮询周期**,连续自然不喷泉。
- **省流抓取** —— 支持 gzip 和 ETag / Last-Modified 条件请求，未更新时不重复下载正文。默认轮询 120 秒；省流开关开启时，低频源（日报、榜单类）单独降频，其他源沿用轮询间隔。验证器只存在当前进程内，重启会重新拉取。
- **自建 RSSHub 中转支持** —— 支持填写你自建的 RSSHub 地址(自签证书、basic-auth 均可)。能接哪些源,取决于你的实例和各源自己的条款。
- **B站式设置面板** —— 分区卡片 + 拨动开关 + 全滑块统一;**弹幕区域可视指引图**(拖动实时看满屏/半屏);**6 套经典主题可切换**(Tokyo Night / Dracula / Gruvbox / Solarized / One Dark / Catppuccin)。
- **精细过滤** —— 按来源逐个开关、关键词白名单 / 屏蔽词、彩色弹幕、描边加粗、长弹幕自适应加速。
- **多屏友好** —— 限定主屏 / 指定屏 / 横贯全部,弹幕不越界乱飞到外接显示器。
- **交互** —— 左键开原文、中键复制标题、右键菜单、悬停暂停;系统托盘控制;**启动自动检查更新**。

---

## 📦 安装

### 下载现成版本(推荐)

到 [**Releases**](https://github.com/widechaos/Bulletin/releases/latest) 下载对应平台:

| 平台 | 文件 | 说明 |
|------|------|------|
| macOS | `Bulletin-macOS.zip` | 解压得 `Bulletin.app`,拖进「应用程序」。首次打开若拦截:右键→打开。菜单栏 agent,无 Dock 图标。 |
| Windows | `Bulletin-Windows.zip` | 解压运行 `Bulletin.exe`。 |
| Linux | `Bulletin-Linux.tar.gz` | 解压运行 `Bulletin/Bulletin`。 |

### 从源码运行

```bash
git clone https://github.com/widechaos/Bulletin.git
cd Bulletin
pip install -r requirements.txt
python main.py
```

---

## ⚙️ 配置

首次运行生成配置文件:

- 源码运行 → 程序目录下 `config.json`
- 打包版 → 用户目录(macOS `~/Library/Application Support/Bulletin/`,Windows `%APPDATA%\Bulletin\`,Linux `~/.config/Bulletin/`)

大部分设置在**托盘 → 设置**里图形化调整。预设只含发布方自己公开的 RSS(科技、财经、美股、币圈、English)。用自建 RSSHub 时在设置里填 **RSSHub 基址**,feeds 里的 `{rsshub}` 会自动替换成它。

> ⚠️ `config.json` 可能含私有中转地址 / 凭据,已在 `.gitignore` 中,不会进仓库。

---

## 🛠 自行打包

```bash
pip install -r requirements.txt pyinstaller
pyinstaller Bulletin.spec --noconfirm
# 产物在 dist/ —— mac 为 Bulletin.app,win/linux 为 Bulletin/ 目录
```

跨平台发布由 GitHub Actions 完成:推一个 `v*` tag(如 `git tag v0.1.0 && git push origin v0.1.0`)即自动为三大平台构建并发布到 Releases。

---

## 📄 License

[PolyForm Noncommercial 1.0.0](LICENSE):个人学习、研究、自用及非营利组织可免费使用和修改;**任何商业用途(含公司内部使用、二次分发牟利、集成进商业产品)都需要另取得商业授权**,请到 [widechaos.cn/bulletin/pro](https://widechaos.cn/bulletin/pro) 联系。

2026-10-17 改许可之前已公开的代码与版本(截至提交 d13f793)仍按当时的 MIT 许可适用于那些版本。
