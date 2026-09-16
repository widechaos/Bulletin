# DangmuNews · 桌面弹幕新闻

让新闻**以弹幕的形式直接飞在你的桌面上**——透明、置顶、**鼠标穿透不挡任何程序的操作**；只有当你把指针移到某条弹幕上时它才可点：**左键打开原文，右键开设置**。

新闻源用 **RSS / RSSHub**：无需任何账号或鉴权，几乎能接入任何来源（新闻站、微博热搜、知乎热榜、公众号、Hacker News…）。基于 Python + Qt（PySide6），跨平台（macOS / Windows / Linux）。

## 特性

- **真·桌面弹幕**：不是窗口。每条弹幕是独立的透明置顶小窗，飞过整个桌面，互不影响你正常点击、操作其它程序。
- **可交互**：指针悬停到弹幕上→它变可点，**左键跳转新闻原文**，**右键打开设置**。
- **RSS / RSSHub 源**：`config.json` 里加 RSS 链接即可；用 [RSSHub](https://docs.rsshub.app) 可把微博/知乎/B站/公众号等变成 RSS。
- **系统托盘控制**：暂停 / 清屏 / 设置 / 退出。
- **多泳道并发**、可调字体/字号/颜色/速度/透明度/占屏高度。

## 快速开始

```bash
git clone https://github.com/widechaos/DangmuNews.git
cd DangmuNews
pip install -r requirements.txt
python main.py
```

首次运行会生成 `config.json`（含一组默认 RSS 源），弹幕随即开始飞。用**系统托盘图标**（蓝色“弹”）打开设置或退出。

## 配置新闻源

编辑 `config.json` 的 `feeds`（或托盘 → 设置 里改），每行一个 RSS/Atom 链接：

```json
"feeds": [
  "https://news.ycombinator.com/rss",
  "https://sspai.com/feed",
  "https://rsshub.app/zhihu/hotlist",
  "https://rsshub.app/weibo/search/hot"
]
```

> 想接**任何**没有原生 RSS 的站点？用 RSSHub（公共实例 `https://rsshub.app` 或自建），在它的路由列表里找到对应源，拿到 RSS 链接加进来即可。

## 操作

- **看**：弹幕自动从右向左飞过桌面上部。
- **点**：把指针移到某条弹幕上（会出现下划线），左键打开原文，右键打开设置。
- **控制**：系统托盘图标 → 暂停 / 清屏 / 设置 / 退出。

## 其它设置（`config.json`）

| 键 | 说明 |
|---|---|
| `font_size` / `font_color` / `font_family` | 字号 / 颜色 / 字体（空=按平台自动） |
| `speed` | 弹幕速度（像素/帧） |
| `opacity` | 单条弹幕不透明度 |
| `top_fraction` | 弹幕占屏幕高度的上部比例 |
| `max_on_screen` | 同屏最多弹幕数 |
| `poll_interval` | 新闻源轮询间隔（秒） |

## 打包

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name DangmuNews main.py
```

## 环境

- Python 3.9+
- 依赖：`PySide6`、`feedparser`（见 `requirements.txt`）

## 许可

MIT，见 [LICENSE](LICENSE)。
