# -*- mode: python ; coding: utf-8 -*-
import os
import rapidocr_onnxruntime

# 动态获取 rapidocr_onnxruntime 安装路径（避免硬编码本地绝对路径，便于在 CI 等其他环境构建）
_rapidocr_dir = os.path.dirname(rapidocr_onnxruntime.__file__)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('gui/icons', 'gui/icons'),
        ('configs', 'configs'),
        (os.path.join(_rapidocr_dir, 'config.yaml'), 'rapidocr_onnxruntime'),
        (os.path.join(_rapidocr_dir, 'models'), 'rapidocr_onnxruntime/models'),
    ],
    hiddenimports=['PyQt6.sip'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['easyocr', 'torch', 'torchvision', 'torchaudio'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='QQFarmBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)
