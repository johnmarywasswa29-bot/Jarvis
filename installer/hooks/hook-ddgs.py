from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("ddgs", include_py_files=False)
hiddenimports = collect_submodules("ddgs")
