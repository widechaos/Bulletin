"""配置:JSON 存储。弹幕样式/区域/密度/过滤/显示器 + RSS 源与预设。"""
import json
import os
import sys

APP_NAME = "DangmuNews"
__version__ = "0.1.0"


def _data_dir() -> str:
    """配置/去重文件的落地目录。
    打包后(frozen)程序目录只读 → 写用户目录;开发时放脚本旁,沿用现有 config.json。"""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        elif sys.platform.startswith("win"):
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        else:
            base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        d = os.path.join(base, APP_NAME)
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.dirname(os.path.abspath(__file__))


BASE = _data_dir()
CONFIG_FILE = os.path.join(BASE, "config.json")
SEEN_FILE = os.path.join(BASE, ".seen.json")


def default_font_family() -> str:
    if sys.platform == "darwin":
        return "PingFang SC"
    if sys.platform.startswith("win"):
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"


# 预设新闻源。"原生"= 直接 RSS,稳;"RSSHub"= 需可达的 RSSHub 实例(公共实例常被墙,建议自建)
PRESETS = {
    "科技中文(原生)": [
        "https://www.ithome.com/rss/",
        "https://www.cnbeta.com.tw/backend.php",
        "https://sspai.com/feed",
        "https://www.solidot.org/index.rss",
    ],
    "财经": [
        "https://dedicated.wallstreetcn.com/rss.xml",   # 华尔街见闻(原生)
    ],
    "English": [
        "https://news.ycombinator.com/rss",
        "https://www.theverge.com/rss/index.xml",
        "http://feeds.bbci.co.uk/news/world/rss.xml",
    ],
    "实时·RSSHub": [
        "{rsshub}/cls/telegraph",      # 财联社电报(秒级)
        "{rsshub}/wallstreetcn/live",  # 华尔街见闻快讯
        "{rsshub}/weibo/search/hot",   # 微博热搜
        "{rsshub}/zhihu/daily",        # 知乎日报
    ],
}
# feeds 里的 {rsshub} 会被替换成 rsshub_base(自建实例填这个)

SOURCE_PALETTE = [
    "#38E1F0", "#F5B54B", "#7DE38B", "#FF8FB1", "#9B8CFF",
    "#5AC8FF", "#FF9F6E", "#6EE7D6", "#E7C16E", "#C08CFF",
]

DEFAULTS = {
    "font_family": "",
    "font_size": 30,
    "font_color": "#FFFFFF",
    "opacity": 0.95,
    "speed": 2.4,
    "top_offset": 0.05,
    "top_fraction": 0.55,
    "max_on_screen": 60,
    "display": "primary",       # primary | span(横贯全部) | 某个屏幕名
    "theme": "Tokyo Night",     # 设置面板主题(见 overlay.THEMES);仅影响设置窗外观
    "rsshub_base": "https://rsshub.app",   # 自建 RSSHub 填这里(如 https://你的域名);feeds 里 {rsshub} 会替换成它
    # 实时性
    "poll_interval": 60,
    "emit_backlog": False,
    "startup_count": 22,        # 启动渐进放出的近期条数(由队列按频率慢慢吐,不是一股脑)
    "spawn_interval_ms": 1400,  # 基础出现间隔;实际按积压量动态节流 + 抖动 → 自然流,不喷泉
    "per_feed_limit": 20,
    # 交互与显示
    "hover_pause": True,
    "show_source_tag": True,
    "show_time": True,          # 显示时间戳(几分钟前 / 详细时间)
    "colorful": False,          # 彩色弹幕:整条按来源上色(否则正文白色)
    "outline": 2,               # 描边/加粗强度(0=无描边,越大越粗黑边)
    "adaptive_speed": False,
    "adaptive_max_speed": 4.0,
    # 过滤
    "mute_keywords": [],
    "muted_sources": [],        # 关掉的新闻源(按来源开关)
    "only_keywords": [],        # 白名单关键词
    "only_enabled": False,      # 启用白名单(只看含这些词的)
    # 其它(下一批实现)
    "autostart": False,
    "pause_on_fullscreen": True,
    "feeds": list(PRESETS["科技中文(原生)"]) + list(PRESETS["财经"]),
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    if not cfg.get("font_family"):
        cfg["font_family"] = default_font_family()
    save(cfg)
    return cfg


def save(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_seen() -> set:
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError):
        return set()


def save_seen(seen) -> None:
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen)[-4000:], f)
    except OSError:
        pass
