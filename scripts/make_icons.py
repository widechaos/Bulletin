"""从 assets/icon.svg 生成 macOS .icns / Windows .ico / 通用 PNG。
用法:python scripts/make_icons.py
"""
import os
import subprocess
import sys

from PySide6 import QtCore, QtGui
from PySide6.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "assets", "icon.svg")
ASSETS = os.path.join(ROOT, "assets")


def render(size: int) -> QtGui.QImage:
    img = QtGui.QImage(size, size, QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    r = QSvgRenderer(SVG)
    p = QtGui.QPainter(img)
    r.render(p)
    p.end()
    return img


def main():
    app = QtGui.QGuiApplication(sys.argv)  # noqa: F841  (QImage/QPainter 需要)
    os.makedirs(ASSETS, exist_ok=True)

    # 通用大图
    render(1024).save(os.path.join(ASSETS, "icon_1024.png"))
    render(512).save(os.path.join(ASSETS, "icon_512.png"))

    # macOS .icns
    iconset = os.path.join(ASSETS, "icon.iconset")
    os.makedirs(iconset, exist_ok=True)
    specs = [(16, ""), (16, "@2x"), (32, ""), (32, "@2x"),
             (128, ""), (128, "@2x"), (256, ""), (256, "@2x"), (512, ""), (512, "@2x")]
    base = {"": 1, "@2x": 2}
    for s, suf in specs:
        px = s * base[suf]
        render(px).save(os.path.join(iconset, f"icon_{s}x{s}{suf}.png"))
    try:
        subprocess.run(["iconutil", "-c", "icns", iconset,
                        "-o", os.path.join(ASSETS, "icon.icns")], check=True)
        print("✓ icon.icns")
    except Exception as e:
        print("icns 失败(非 mac 可忽略):", e)

    # Windows .ico
    try:
        from PIL import Image
        pngs = [os.path.join(ASSETS, f"_ico_{s}.png") for s in (256, 128, 64, 48, 32, 16)]
        for s, pth in zip((256, 128, 64, 48, 32, 16), pngs):
            render(s).save(pth)
        imgs = [Image.open(p) for p in pngs]
        imgs[0].save(os.path.join(ASSETS, "icon.ico"), format="ICO",
                     sizes=[(i.width, i.height) for i in imgs])
        for p in pngs:
            os.remove(p)
        print("✓ icon.ico")
    except Exception as e:
        print("ico 失败(缺 Pillow?):", e)


if __name__ == "__main__":
    main()
