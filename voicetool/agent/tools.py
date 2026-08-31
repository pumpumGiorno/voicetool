"""Typed Stage 2 registry: native tools only, never arbitrary process execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cancellation import AgentCancelled, CancellationToken
from .types import ErrorCode, ToolCall, ToolResult


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable
    needs_token: bool = False

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
            result = ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION,
                                     f"Неизвестный tool: {call.name}")
            result.tool = str(call.name)
            return result
        try:
            arguments = validate_arguments(spec.parameters, call.arguments)
            token.raise_if_cancelled()
            output = (spec.handler(token=token, **arguments) if spec.needs_token
                      else spec.handler(**arguments))
            result = output if isinstance(output, ToolResult) else ToolResult.ok(data=output or {})
        except AgentCancelled:
            raise
        except ValueError as exc:
            result = ToolResult.fail(ErrorCode.INVALID_ARGUMENT, str(exc))
        except PermissionError as exc:
            result = ToolResult.fail(ErrorCode.ACCESS_DENIED, str(exc))
        except Exception as exc:
            result = ToolResult.fail(ErrorCode.UNSUPPORTED_ACTION, f"{call.name}: {exc}")
        result.tool = str(call.name)
        return result

    def _add(self, name, description, properties=None, required=(), handler=None,
             *, needs_token=False):
        schema = {"type": "object", "properties": properties or {},
                  "required": list(required), "additionalProperties": False}
        self._tools[name] = ToolSpec(name, description, schema, handler, needs_token)

    def _register_defaults(self):
        def string(description, max_length=260):
            return {"type": "string", "description": description, "maxLength": max_length}

        def integer(description, low, high):
            return {"type": "integer", "description": description,
                    "minimum": low, "maximum": high}

        def array(description):
            return {"type": "array", "description": description,
                    "items": {"type": "string"}, "minItems": 1, "maxItems": 5}

        c = self.controller
        self._add("resolve_app", "Resolve an application name without launching it.",
                  {"app_name": string("Application name")}, ("app_name",), c.resolve_app)
        self._add("list_installed_apps", "List locally discovered applications.",
                  handler=c.list_installed_apps)
        self._add("launch_app", "Open an app, or focus its existing window.",
                  {"app_name": string("Application name")}, ("app_name",), c.launch_app,
                  needs_token=True)
        self._add("focus_app", "Focus an existing application window.",
                  {"app_name": string("Application name")}, ("app_name",), c.focus_app)
        self._add("close_app", "Request normal close of an application window.",
                  {"app_name": string("Application name")}, ("app_name",), c.close_app)
        self._add("get_running_apps", "List applications with visible windows.",
                  handler=c.get_running_apps)

        self._add("list_windows", "List visible top-level windows.", handler=c.list_windows)
        self._add("get_active_window", "Get the verified foreground window.",
                  handler=c.get_active_window)
        for name, description, handler in (
            ("focus_window", "Focus and verify an existing window.", c.focus_window),
            ("minimize_window", "Minimize an existing window.", c.minimize_window),
            ("maximize_window", "Maximize an existing window.", c.maximize_window),
            ("restore_window", "Restore a minimized/maximized window.", c.restore_window),
            ("close_window", "Request normal close of a window.", c.close_window),
        ):
            self._add(name, description, {"window": string("Window title or HWND")},
                      ("window",), handler)

        self._add("open_url", "Open an allowed URL in the default browser.",
                  {"url": string("http/https URL", 4096)}, ("url",), c.open_url)
        self._add("type_text", "Type Unicode text with Windows Unicode input, not clipboard.",
                  {"text": string("Unicode text", 20_000)}, ("text",), c.type_text)
        self._add("press_key", "Press one non-submit keyboard key.",
                  {"key": string("Supported key name", 32)}, ("key",), c.press_key)
        self._add("press_keys", "Press a validated non-submit keyboard shortcut.",
                  {"keys": array("Ordered key names")}, ("keys",), c.press_keys)

        self._add("get_volume", "Get Windows master volume and mute state.",
                  handler=c.get_volume)
        self._add("set_volume", "Set Windows master volume exactly.",
                  {"percent": integer("Volume percentage", 0, 100)},
                  ("percent",), c.set_volume)
        self._add("change_volume", "Change Windows master volume by a signed percentage.",
                  {"delta": integer("Signed percentage", -100, 100)},
                  ("delta",), c.change_volume)
        self._add("mute_volume", "Mute Windows master audio.", handler=c.mute_volume)
        self._add("unmute_volume", "Unmute Windows master audio.", handler=c.unmute_volume)

        self._add("open_file", "Open an existing file with its default application.",
                  {"path": string("Existing file path", 32_000)}, ("path",), c.open_file)
        self._add("open_folder", "Open an existing folder or a known-folder alias.",
                  {"path": string("Folder path or alias", 32_000)}, ("path",), c.open_folder)
        self._add("find_file", "Search safe user folders by file-name fragment.", {
            "query": string("File-name fragment"),
            "root": string("Optional search root", 32_000),
            "max_results": integer("Maximum result count", 1, 100),
        }, ("query",), c.find_file, needs_token=True)
        self._add("show_in_folder", "Reveal an existing file in Windows Explorer.",
                  {"path": string("Existing path", 32_000)}, ("path",), c.show_in_folder)


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
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name}: ожидалась непустая строка")
            if len(value) > int(rule.get("maxLength", 32_000)):
                raise ValueError(f"{name}: значение слишком длинное")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name}: ожидалось целое число")
            if value < rule["minimum"] or value > rule["maximum"]:
                raise ValueError(f"{name}: значение вне допустимого диапазона")
        elif expected == "array":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{name}: ожидался массив строк")
            if not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", 10_000):
                raise ValueError(f"{name}: некорректное число элементов")
        output[name] = value
    return output
