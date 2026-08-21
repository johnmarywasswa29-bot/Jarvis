import glob
import os

from PyInstaller.utils.hooks import collect_data_files

# ddgs is installed/imported as 'ddgs' (current package). Earlier versions used
# 'duckduckgo_search' / 'duckduckgo-search'. We only need the modules required
# for DDGS().text(...) web search — NOT the optional api_server (fastapi/
# uvicorn), cli (click), dht (trio), mcp extras, and NOT the non-default search
# engines (bing/brave/google/annasarchive/...), several of which do import-time
# work that hangs PyInstaller's Analysis.
#
# Build the submodule list by FILESYSTEM SCAN (no importing), so the hook
# itself cannot hang or pull heavy optional deps into the analysis graph.

# Engine modules required for the keyless multi-backend research fallback
# (preferred order: duckduckgo -> startpage -> mojeek -> yahoo). bing is
# disabled=True in ddgs and omitted. brave/google/yandex/annasarchive/... are
# NOT collected (they are not part of the authorized fallback and several do
# import-time work that hangs PyInstaller's Analysis). The ddgs engine registry
# (engines/__init__.py) auto-discovers only the modules physically present in
# the frozen ddgs/engines/ dir, so collecting exactly these is both necessary
# and sufficient.
_SAFE_ENGINES = (
    "ddgs.engines.duckduckgo",
    "ddgs.engines.duckduckgo_news",
    "ddgs.engines.duckduckgo_images",
    "ddgs.engines.startpage",
    "ddgs.engines.mojeek",
    "ddgs.engines.yahoo",
)

# Submodule prefixes that are NOT required for the search path and are excluded.
_EXCLUDE_PREFIXES = (
    "ddgs.api_server",
    "ddgs.cli",
    "ddgs.dht",
    "ddgs.mcp",
    "ddgs.engines.annasarchive",
    "ddgs.engines.bing",
    "ddgs.engines.brave",
    "ddgs.engines.google",
    "ddgs.engines.grokipedia",
    "ddgs.engines.wikipedia",
    "ddgs.engines.yandex",
)


def _pkg_submodules_by_scan(pkg_name):
    """Importable submodule names of an installed package via glob (no import)."""
    import importlib.util

    spec = importlib.util.find_spec(pkg_name)
    if spec is None or not spec.submodule_search_locations:
        return []
    root = spec.submodule_search_locations[0]
    mods = [pkg_name]
    for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, root)
        if rel == "__init__.py":
            continue
        mod = pkg_name + "." + rel[: -len(".py")].replace(os.sep, ".")
        if mod.endswith(".__main__"):
            continue
        mods.append(mod)
    return mods


def _search_submodules(pkg_name, safe_engines=()):
    kept = []
    for mod in _pkg_submodules_by_scan(pkg_name):
        # For the engines subpackage, keep ONLY the explicitly safe engines;
        # every other engine (bing/brave/google/annasarchive/yahoo/videos/...)
        # is excluded to avoid import-time hangs during Analysis.
        if pkg_name == "ddgs" and mod.startswith("ddgs.engines.") and mod not in safe_engines:
            continue
        if any(mod == p or mod.startswith(p + ".") for p in _EXCLUDE_PREFIXES):
            continue
        kept.append(mod)
    # Always ensure the core + required engines are present.
    for core in (pkg_name, f"{pkg_name}.ddgs", f"{pkg_name}.base", *safe_engines):
        if core not in kept:
            kept.append(core)
    return kept


hiddenimports = []
hiddenimports += _search_submodules("ddgs", _SAFE_ENGINES)
hiddenimports += _search_submodules("primp")
hiddenimports += _search_submodules("duckduckgo_search")
hiddenimports += _search_submodules("fake_useragent")

# Preserve package data files (e.g. primp's bundled root-CA / config assets, and
# fake_useragent's browsers.jsonl UA dataset that ddgs relies on for its
# randomized User-Agent header).
datas = []
datas += collect_data_files("ddgs", include_py_files=False)
datas += collect_data_files("primp", include_py_files=False)
datas += collect_data_files("duckduckgo_search", include_py_files=False)
datas += collect_data_files("fake_useragent", include_py_files=False)
