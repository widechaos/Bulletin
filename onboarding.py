"""首次启动引导:选想看的快讯、填一个关注词,一页走完。

在 RSS 抓取启动之前弹出,选完才开始抓,这样第一批弹幕就是用户选的源。
关掉窗口等于全用默认,下次不再弹。
"""
from __future__ import annotations

from PySide6 import QtCore, QtWidgets

import config as cfg

# (预设名, 显示名, 一句说明);预设内容在 config.PRESETS
CHOICES = (
    ("财经", "财经快讯", "华尔街见闻"),
    ("美股", "美股", "MarketWatch、CNBC"),
    ("币圈", "币圈 7×24", "CoinDesk、Cointelegraph、The Block、Decrypt"),
    ("科技", "科技", "IT之家、cnBeta、少数派、Solidot"),
    ("English", "English 国际新闻", "Hacker News、The Verge、BBC"),
)
WELCOME = "欢迎使用 Bulletin:快讯会从这里飞过,指针停上去会暂停,左键击碎,右键复制"


class Onboarding(QtWidgets.QDialog):
    def __init__(self, conf: dict, qss: str):
        super().__init__()
        self.conf = conf
        self.setWindowTitle("Bulletin · 开始使用")
        self.setStyleSheet(qss)
        self.setMinimumWidth(520)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 20)
        v.setSpacing(10)

        title = QtWidgets.QLabel("欢迎使用 Bulletin")
        title.setStyleSheet("font-size:20px;font-weight:600;background:transparent;")
        v.addWidget(title)
        v.addWidget(self._hint("选你想在桌面上看的快讯,可以多选,之后在设置里随时改。"))

        self.checks = {}
        for key, name, desc in CHOICES:
            cb = QtWidgets.QCheckBox(f"{name}   ·  {desc}")
            cb.setChecked(key in ("财经", "科技"))
            self.checks[key] = cb
            v.addWidget(cb)
        v.addWidget(self._hint("A 股实时快讯要接你自建的 RSSHub,在 设置 → 新闻源 里填。"))

        v.addSpacing(8)
        v.addWidget(self._label("关注词(选填)"))
        self.kw = QtWidgets.QLineEdit()
        v.addWidget(self.kw)
        self.kw_hint = self._hint("")
        v.addWidget(self.kw_hint)

        self.kw.setPlaceholderText("填一个你最关心的词,如 降准、英伟达")
        self.kw_hint.setText("命中的快讯红底插队、响一声。社区版 1 个,会员版不限个数。")

        v.addSpacing(6)
        go = QtWidgets.QPushButton("开始")
        go.setDefault(True)
        go.clicked.connect(self.accept)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(go)
        v.addLayout(row)

    def _hint(self, text):
        lb = QtWidgets.QLabel(text)
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#8A8A8E;background:transparent;")
        return lb

    def _label(self, text):
        lb = QtWidgets.QLabel(text)
        lb.setStyleSheet("font-weight:600;background:transparent;")
        return lb

    def apply(self):
        """把选择写进配置;返回要先放的两条欢迎弹幕(标题列表)。"""
        feeds = []
        for key, cb in self.checks.items():
            if cb.isChecked():
                feeds += [u for u in cfg.PRESETS[key] if u not in feeds]
        if feeds:
            self.conf["feeds"] = feeds
        words = [w.strip() for w in self.kw.text().replace("，", ",").split(",") if w.strip()][:1]
        if words:
            self.conf["alert_keywords"] = words
        self.conf["onboarded"] = True
        cfg.save(self.conf)
        titles = [WELCOME]
        if words:
            titles.append(f"示例:「{words[0].split('|')[0]}」出现在快讯里时,会这样红底插队、响一声")
        return titles


def run(conf: dict, qss: str) -> list:
    """弹出引导;关掉窗口也算走过(全用默认)。返回欢迎弹幕标题。"""
    dlg = Onboarding(conf, qss)
    dlg.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.exec()
    return dlg.apply()
