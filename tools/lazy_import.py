"""Load optional dependencies only when the code path that needs them runs."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


def lazy_import(module_name: str) -> ModuleType:
    """Import and return a module on demand; Python caches it after first load."""
    return import_module(module_name)
