from __future__ import annotations

import sys as _sys

from novelist.workflows import adaptation as _impl

for _name in _impl.__all__:
    if not _name.startswith("_"):
        globals()[_name] = getattr(_impl, _name)


def _sync_impl_namespace() -> None:
    _impl.sync_namespace(globals())


def _make_compat_wrapper(_name: str):
    def _wrapper(*args, **kwargs):
        _sync_impl_namespace()
        return getattr(_impl, _name)(*args, **kwargs)
    _wrapper.__name__ = _name
    _wrapper.__qualname__ = _name
    _wrapper.__doc__ = getattr(getattr(_impl, _name), "__doc__", None)
    _wrapper.__workflow_facade_wrapper__ = True
    return _wrapper


for _name in getattr(_impl, "_FUNCTION_NAMES", []):
    globals()[_name] = _make_compat_wrapper(_name)

__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    raise SystemExit(main())
