from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _runtime_module(module: str) -> bool:
    return ".tests" not in module and ".benchmarks" not in module


hiddenimports = collect_submodules("sympy", filter=_runtime_module)
datas = collect_data_files("sympy")
