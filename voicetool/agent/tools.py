"""Typed, validated tool registry. It intentionally has no shell capability."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cancellation import AgentCancelled, CancellationToken
from .types import ToolCall, ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable

    def ollama_schema(self):
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


class ToolRegistry:
    def __init__(self, controller):
        self.controller = controller
        self._tools: dict[str, ToolSpec] = {}
        self._register_defaults()

    @property
    def names(self):
        return tuple(self._tools)

    def schemas(self):
        return [spec.ollama_schema() for spec in self._tools.values()]

    def execute(self, call: ToolCall, token: CancellationToken) -> ToolResult:
        token.raise_if_cancelled()
        spec = self._tools.get(str(call.name))
        if not spec:
            result = ToolResult.fail("tool_not_found", f"Неизвестный tool: {call.name}")
            result.tool = str(call.name)
            return result
        try:
            arguments = validate_arguments(spec.parameters, call.arguments)
            token.raise_if_cancelled()
            output = spec.handler(**arguments)
            result = output if isinstance(output, ToolResult) else ToolResult.ok(data=output or {})
        except AgentCancelled:
            raise
        except ValueError as exc:
            result = ToolResult.fail("invalid_arguments", str(exc))
        except Exception as exc:
            result = ToolResult.fail("tool_exception", f"{call.name}: {exc}")
        result.tool = str(call.name)
        return result

    def _add(self, name, description, properties, required, handler):
        schema = {"type": "object", "properties": properties,
                  "required": list(required), "additionalProperties": False}
        self._tools[name] = ToolSpec(name, description, schema, handler)

    def _register_defaults(self):
        string = lambda description: {"type": "string", "description": description, "maxLength": 200}
        integer = lambda description, low, high: {
            "type": "integer", "description": description, "minimum": low, "maximum": high,
        }
        c = self.controller
        self._add("launch_app", "Open or focus a discovered Windows application.",
                  {"app_name": string("Application name")}, ("app_name",), c.launch_app)
        self._add("focus_app", "Focus an existing application window.",
                  {"app_name": string("Application name")}, ("app_name",), c.focus_app)
        self._add("close_app", "Request normal close of an application window.",
                  {"app_name": string("Application name")}, ("app_name",), c.close_app)
        self._add("get_running_apps", "List visible application windows.", {}, (), c.get_running_apps)
        self._add("get_volume", "Read Windows master volume.", {}, (), c.get_volume)
        self._add("set_volume", "Set Windows master volume percentage.",
                  {"percent": integer("0 to 100", 0, 100)}, ("percent",), c.set_volume)
        self._add("change_volume", "Change Windows master volume by a signed percentage.",
                  {"delta": integer("-100 to 100", -100, 100)}, ("delta",), c.change_volume)


def validate_arguments(schema: dict, arguments: Any) -> dict:
    if not isinstance(arguments, dict):
        raise ValueError("Аргументы tool должны быть JSON object")
    properties = schema.get("properties", {})
    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        raise ValueError("Не хватает аргументов: " + ", ".join(missing))
    unknown = set(arguments) - set(properties)
    if unknown:
        raise ValueError("Неизвестные аргументы: " + ", ".join(sorted(unknown)))
    output = {}
    for name, value in arguments.items():
        rule = properties[name]
        expected = rule.get("type")
        if expected == "string":
            if not isinstance(value, str) or not value.strip() or len(value) > rule.get("maxLength", 200):
                raise ValueError(f"{name}: ожидалась непустая строка")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name}: ожидалось целое число")
            if value < rule["minimum"] or value > rule["maximum"]:
                raise ValueError(f"{name}: значение вне допустимого диапазона")
        output[name] = value
    return output
