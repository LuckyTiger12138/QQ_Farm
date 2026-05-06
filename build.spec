# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('gui/icons', 'gui/icons'),
        ('configs', 'configs'),
        ('C:/Users/FuXing/AppData/Local/Programs/Python/Python312/Lib/site-packages/rapidocr_onnxruntime/config.yaml', 'rapidocr_onnxruntime'),
        ('C:/Users/FuXing/AppData/Local/Programs/Python/Python312/Lib/site-packages/rapidocr_onnxruntime/models', 'rapidocr_onnxruntime/models'),
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
