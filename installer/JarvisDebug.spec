# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['..\\jarvis.py'],
    pathex=[],
    binaries=[],
    datas=[('C:/Users/User NA/Desktop/jarvis/config.yaml', '.'), ('C:/Users/User NA/Desktop/jarvis/requirements.txt', '.'), ('C:/Users/User NA/Desktop/jarvis/README.md', '.'), ('C:/Users/User NA/Desktop/jarvis/assets', 'assets'), ('C:/Users/User NA/Desktop/jarvis/knowledge', 'knowledge'), ('C:/Users/User NA/Desktop/jarvis/plugins', 'plugins'), ('C:/Users/User NA/Desktop/jarvis/data', 'data'), ('C:/Users/User NA/Desktop/jarvis/logs', 'logs')],
    hiddenimports=['PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'],
    hookspath=['installer/hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['webrtcvad', 'torch', 'torchvision', 'torchaudio', 'tensorboard'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JarvisDebug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\jarvis.ico'],
)
