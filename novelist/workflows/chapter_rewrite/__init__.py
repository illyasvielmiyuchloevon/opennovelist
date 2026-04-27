from __future__ import annotations

from . import _shared as _shared
from . import models as models
from . import review_payloads as review_payloads
from . import project as project
from . import state as state
from . import catalog as catalog
from . import prompt_summary as prompt_summary
from . import responses as responses
from . import document_repair as document_repair
from . import review as review
from . import prompts as prompts
from . import chapter_runner as chapter_runner
from . import group_runner as group_runner
from . import volume_runner as volume_runner
from . import runner as runner

_MODULES = [
    models,
    review_payloads,
    project,
    state,
    catalog,
    prompt_summary,
    responses,
    document_repair,
    review,
    prompts,
    chapter_runner,
    group_runner,
    volume_runner,
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
