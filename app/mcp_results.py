from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MCPTextResult:
    text: str
    structured_content: dict[str, Any] | list[Any] | None = None
