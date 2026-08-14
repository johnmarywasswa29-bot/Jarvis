from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("vosk", include_py_files=False)
binaries = collect_dynamic_libs("vosk")
