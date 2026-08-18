from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# The package is installed as 'duckduckgo-search' but imported as 'ddgs' (for older versions)
# The new version uses 'duckduckgo_search' as the import name
datas = collect_data_files("duckduckgo_search", include_py_files=False)
hiddenimports = collect_submodules("duckduckgo_search")
