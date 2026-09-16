"""在线更新:比对 GitHub 最新 release 与本地版本。

MVP = 检查 + 引导下载(打开 release 页)。不做静默替换(跨平台自替换风险高,留作后续)。
"""
from __future__ import annotations

import json
import ssl
import urllib.request

from config import __version__

REPO = "widechaos/Bulletin"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE_LATEST = f"https://github.com/{REPO}/releases/latest"


def _norm(v: str) -> tuple:
    v = (v or "").lstrip("vV").strip()
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def check_update(timeout: int = 8) -> tuple[bool, str, str]:
    """返回 (是否有新版, 最新 tag, 下载页 url)。网络失败返回 (False, '', 页面)。"""
    try:
        req = urllib.request.Request(
            API_LATEST,
            headers={"User-Agent": "DangmuNews-updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            data = json.loads(r.read())
        tag = data.get("tag_name", "")
        url = data.get("html_url") or PAGE_LATEST
        return (_norm(tag) > _norm(__version__), tag, url)
    except Exception:
        return (False, "", PAGE_LATEST)
