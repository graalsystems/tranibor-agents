# SPDX-License-Identifier: Apache-2.0
"""The former import name of `tranibor_servicenow`, kept for the window of the core's ADR 0226.

`choregos_servicenow` is the package `tranibor_servicenow` itself, not a copy: `sys.modules` maps the former
name to the package, and a finder put first on `sys.meta_path` answers every `choregos_servicenow.<module>`
with the module `tranibor_servicenow.<module>`, so a file that still names `choregos_servicenow.app:build_app`
(the core's in-process demonstration, its ADR 0218) builds the same application with the same classes, and
`python -m choregos_servicenow.<module>` runs the module's code. Importing it warns once. It goes when the
plugin requires a core past the window (0.32.0).
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
import warnings
from collections.abc import Sequence

import tranibor_servicenow

FORMER, NEW = "choregos_servicenow", "tranibor_servicenow"


class _Loader(importlib.abc.InspectLoader):
    """Hands the import system the module `tranibor_servicenow.<module>` itself; gives runpy its code."""

    def __init__(self, real: importlib.machinery.ModuleSpec) -> None:
        self.real = real

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return importlib.import_module(self.real.name)

    def exec_module(self, module: types.ModuleType) -> None:
        module.__spec__ = self.real  # the module keeps its own spec, whatever name imported it

    def _inspect(self) -> importlib.abc.InspectLoader:
        loader = self.real.loader
        if not isinstance(loader, importlib.abc.InspectLoader):
            raise ImportError(f"{self.real.name} gives no code", name=self.real.name)
        return loader

    def get_code(self, fullname: str) -> types.CodeType | None:
        return self._inspect().get_code(self.real.name)

    def get_source(self, fullname: str) -> str | None:
        return self._inspect().get_source(self.real.name)

    def is_package(self, fullname: str) -> bool:
        return self.real.submodule_search_locations is not None


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: types.ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(FORMER + "."):
            return None
        real = importlib.util.find_spec(NEW + fullname[len(FORMER) :])
        if real is None:
            return None
        return importlib.util.spec_from_loader(
            fullname, _Loader(real), origin=real.origin, is_package=real.submodule_search_locations is not None
        )


if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Finder())
sys.modules[__name__] = tranibor_servicenow
warnings.warn(
    f"`{FORMER}` is now `{NEW}` (the core's ADR 0226): the former name is an alias for the window",
    DeprecationWarning,
    stacklevel=2,
)
