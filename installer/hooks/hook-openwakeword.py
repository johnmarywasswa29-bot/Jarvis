from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Collect openWakeWord data files (models)
datas = collect_data_files("openwakeword", include_py_files=False)

# Collect submodules for hidden imports
hiddenimports = collect_submodules("openwakeword")

# Also include onnxruntime
try:
    from PyInstaller.utils.hooks import collect_data_files as collect_data
    datas += collect_data("onnxruntime", include_py_files=False)
except Exception:
    pass