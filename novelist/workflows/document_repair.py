from __future__ import annotations

from pathlib import Path
from typing import Any


def operation_apply_debug_payload(*, error: Exception, failed_operation: Any, allowed_files: dict[str, Path]) -> dict[str, Any]:
    return {
        "error": str(error),
        "failed_operation": failed_operation,
        "allowed_files": {key: str(path) for key, path in allowed_files.items()},
    }


__all__ = ["operation_apply_debug_payload"]
