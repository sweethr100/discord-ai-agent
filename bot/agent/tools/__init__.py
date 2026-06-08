from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Iterable


ToolHandler = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def __iter__(self) -> Iterable[ToolDefinition]:
        return iter(self._tools.values())
