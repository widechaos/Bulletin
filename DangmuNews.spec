# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置(跨平台)。
mac → DangmuNews.app(菜单栏 agent,无 Dock 图标);win/linux → onedir 可执行。
构建:pyinstaller DangmuNews.spec --noconfirm
"""
import sys

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['updater'],   # updater 是函数内延迟导入,显式声明防漏
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.Qt3DCore',
              'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtMultimedia'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DangmuNews',
    debug=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='DangmuNews')

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='DangmuNews.app',
        icon=None,
        bundle_identifier='cn.widechaos.dangmunews',
        info_plist={
            'LSUIElement': True,                    # 菜单栏 agent,不占 Dock
            'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '0.1.0',
            'NSHighResolutionCapable': True,
        },
    )
