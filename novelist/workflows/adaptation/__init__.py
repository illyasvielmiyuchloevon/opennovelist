from __future__ import annotations

from . import _shared as _shared
from . import models as models
from . import project as project
from . import materials as materials
from . import prompts as prompts
from . import document_generation as document_generation
from . import review as review
from . import group_outlines as group_outlines
from . import runner as runner

_MODULES = [
    models,
    project,
    materials,
    prompts,
    document_generation,
    review,
    group_outlines,
    runner,
]


_EXPORT_NAMES: list[str] = []
_FUNCTION_NAMES: list[str] = []


def _publish_from_module(module: object) -> None:
    names = list(getattr(module, "__all__", []))
    for name in names:
        value = getattr(module, name)
        globals()[name] = value
        if name not in _EXPORT_NAMES:
            _EXPORT_NAMES.append(name)
        if getattr(value, "__code__", None) is not None and getattr(value, "__module__", "").startswith(__name__ + "."):
            if name not in _FUNCTION_NAMES:
                _FUNCTION_NAMES.append(name)


for _name in getattr(_shared, "__all__", []):
    globals()[_name] = getattr(_shared, _name)
    if _name not in _EXPORT_NAMES:
        _EXPORT_NAMES.append(_name)

for _module in _MODULES:
    _publish_from_module(_module)


def sync_namespace(namespace: dict[str, object]) -> None:
    """Sync patched facade names into split module globals before a legacy call."""
    for name in _EXPORT_NAMES:
        if name in namespace:
            value = namespace[name]
            if getattr(value, "__workflow_facade_wrapper__", False):
                continue
            globals()[name] = value
    for module in _MODULES:
        for name in _EXPORT_NAMES:
            if name in globals():
                module.__dict__[name] = globals()[name]


sync_namespace(globals())

__all__ = list(_EXPORT_NAMES) + ["sync_namespace", "_FUNCTION_NAMES"]
