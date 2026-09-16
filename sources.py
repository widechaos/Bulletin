"""新闻源:RSS/RSSHub 实时流。

设计目标 = 财联社电报那种「实时跟踪」,不是把一堆旧闻一股脑倒出来:
- 持久去重(.seen.json):重启不重复放旧闻
- 启动只放最新 primer_count 条打底,其余旧闻标记已读但不显示
- 之后高频轮询,只把「新出现的」条目实时放出
- 屏蔽词过滤
"""
from __future__ import annotations

import base64
import calendar
import html
import queue
import re
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import config as cfg

try:
    import feedparser
except ImportError:
    feedparser = None

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    text = html.unescape(text or "")
    text = _TAG.sub("", text)
    return " ".join(text.split()).strip()


def resolve_url(url: str, rsshub_base: str) -> str:
    """把 feed 里的 {rsshub} 占位替换成实际 RSSHub 基址(自建实例)。"""
    return (url or "").replace("{rsshub}", (rsshub_base or "").rstrip("/"))


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.rsplit("@", 1)[-1].lower()
    except Exception:
        return ""


def fetch_parse(url: str, rsshub_base: str):
    """抓取并解析一个 feed。对「自建 relay 主机」跳过证书校验(自签+basic auth,安全可接受),
    并从 URL 的 user:pass@ 里取出凭据放进 Authorization 头。返回 feedparser 结果(失败抛异常)。"""
    if feedparser is None:
        raise RuntimeError("no feedparser")
    parts = urlsplit(url)
    headers = {"User-Agent": "DangmuNews/2 (+https://widechaos.cn)"}
    netloc = parts.netloc
    if "@" in netloc:
        userinfo, hostonly = netloc.rsplit("@", 1)
        headers["Authorization"] = "Basic " + base64.b64encode(userinfo.encode()).decode()
        netloc = hostonly
    clean = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    insecure = bool(rsshub_base) and _host(url) == _host(rsshub_base)
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    req = urllib.request.Request(clean, headers=headers)
    with urllib.request.urlopen(req, timeout=25, context=ctx) as r:
        content = r.read()
    return feedparser.parse(content)


def probe_feed(url: str, rsshub_base: str) -> tuple[bool, int, str]:
    """检测单个源:返回 (是否可用, 条数, 最新距今描述)。用于设置里「检测」。"""
    if feedparser is None:
        return (False, 0, "无 feedparser")
    real = resolve_url(url, rsshub_base)
    try:
        d = fetch_parse(real, rsshub_base)
        n = len(d.entries)
        if not n:
            return (False, 0, "0 条")
        e0 = d.entries[0]
        ts = e0.get("published_parsed") or e0.get("updated_parsed")
        if ts:
            import calendar as _cal
            age = int(time.time() - _cal.timegm(ts))
            if age < 3600:
                agestr = f"{max(0, age // 60)}分前"
            elif age < 86400:
                agestr = f"{age // 3600}小时前"
            else:
                agestr = f"{age // 86400}天前"
        else:
            agestr = "无时间"
        return (True, n, agestr)
    except Exception as e:
        return (False, 0, str(e)[:40])


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    ts: float = 0.0     # 发布时间(epoch 秒);0 = 未知


class RSSPoller(threading.Thread):
    def __init__(self, conf: dict, out_queue: "queue.Queue[NewsItem]"):
        super().__init__(name="rss-poller", daemon=True)
        self.conf = conf
        self.q = out_queue
        self._stop = threading.Event()
        self._seen = cfg.load_seen()

    def stop(self):
        self._stop.set()
        cfg.save_seen(self._seen)

    def run(self):
        if feedparser is None:
            print("[sources] 未安装 feedparser。pip install feedparser")
            return
        first = True
        while not self._stop.is_set():
            fresh = self._poll()
            if first and not self.conf.get("emit_backlog", False):
                # 启动只放最新 startup_count 条(由 overlay 队列按频率慢慢吐出,渐进不刷屏)
                fresh = fresh[: int(self.conf.get("startup_count", 22))]
            for it in fresh:
                self.q.put(it)
            cfg.save_seen(self._seen)
            first = False
            interval = max(15, int(self.conf.get("poll_interval", 60)))
            for _ in range(interval):
                if self._stop.is_set():
                    return
                time.sleep(1)

    def _poll(self) -> list[NewsItem]:
        mutes = [m.strip().lower() for m in self.conf.get("mute_keywords", []) if m.strip()]
        limit = int(self.conf.get("per_feed_limit", 15))
        fresh: list[tuple] = []
        base = self.conf.get("rsshub_base", "")
        for url in list(self.conf.get("feeds", [])):
            if self._stop.is_set():
                break
            real = resolve_url(url, base)
            try:
                d = fetch_parse(real, base)
            except Exception as e:
                print(f"[sources] 解析失败 {real}: {e}")
                continue
            source = _clean(getattr(d.feed, "title", "")) or url
            for e in d.entries[:limit]:
                key = e.get("id") or e.get("link") or e.get("title", "")
                if not key or key in self._seen:
                    continue
                self._seen.add(key)
                title = _clean(e.get("title", ""))
                if not title:
                    continue
                low = title.lower()
                if any(m in low for m in mutes):
                    continue
                tstruct = e.get("published_parsed") or e.get("updated_parsed")
                epoch = calendar.timegm(tstruct) if tstruct else 0.0   # struct_time 是 UTC
                fresh.append((tstruct, NewsItem(title, e.get("link", ""), source, epoch)))
        # 最新在前
        fresh.sort(key=lambda t: t[0] or time.gmtime(0), reverse=True)
        return [it for _, it in fresh]
