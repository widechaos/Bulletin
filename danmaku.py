"""弹幕数据模型。渲染由 overlay 的单画布 paintEvent 统一完成(不再是独立窗口)。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Danmu:
    text: str
    url: str
    source: str
    source_color: str = "#38E1F0"
    x: float = 0.0          # 左边缘 x(屏内局部坐标)
    y: int = 0              # 顶部 y
    w: int = 0              # 像素宽(含标签+时间)
    h: int = 0
    tag_w: int = 0          # [来源] 部分宽度
    time_str: str = ""      # 时间戳文本(几分钟前 / 详细)
    time_w: int = 0         # 时间部分宽度
    speed: float = 2.4
    paused: bool = False
    pix: object = field(default=None, repr=False)    # 预渲染的整条图(含描边),每帧只贴图
    pad: int = 0            # pix 左右为描边留的边距
    pad_y: int = 0          # pix 上方超出泳道顶的距离
