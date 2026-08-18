# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['jarvis.py'],
    pathex=[],
    binaries=[],
    datas=[('config.yaml', '.'), ('requirements.txt', '.'), ('README.md', '.'), ('assets', 'assets'), ('knowledge', 'knowledge'), ('plugins', 'plugins'), ('data', 'data'), ('logs', 'logs')],
    hiddenimports=['openwakeword', 'openwakeword.model', 'openwakeword.utils', 'onnxruntime', 'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'plugins', 'plugins.sdk', 'plugins.sdk.api', 'plugins.sdk.manager', 'plugins.sdk.events', 'plugins.sdk.loader', 'plugins.sdk.permissions', 'plugins.sdk.registry', 'plugins.sdk.sandbox', 'plugins.sdk.state', 'ddgs', 'duckduckgo_search'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['webrtcvad', 'torch', 'torchvision', 'torchaudio', 'tensorboard', 'scipy', 'sklearn', 'pandas', 'matplotlib', 'PIL', 'numba', 'llvmlite', 'jinja2', 'rich', 'pytest', 'numba', 'tensorflow'],
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
    name='Jarvis',
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
)
