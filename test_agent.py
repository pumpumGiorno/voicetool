"""Dependency-light Stage 1 tests for Alice Local Agent Core."""
import tempfile
import threading
import time
import unittest
import urllib.error
from types import SimpleNamespace

from voicetool import config
from voicetool.agent.cancellation import CancellationToken
from voicetool.agent.desktop import AppResolver
from voicetool.agent.ollama import OllamaProvider
from voicetool.agent.router import RouteKind, VoiceCommandRouter
from voicetool.agent.service import DesktopAgentService
from voicetool.agent.session import AgentSession
from voicetool.agent.tools import ToolRegistry
from voicetool.agent.types import AgentResult, AgentStatus, ErrorCode, ToolCall, ToolResult
from voicetool.engine import Listener


class FakeAutomation:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def handler(**arguments):
            arguments.pop("token", None)
            self.calls.append((name, arguments))
            data = dict(arguments)
            if "app_name" in arguments:
                data["app"] = arguments["app_name"]
            return ToolResult.ok(f"{name} ok", data=data)
        return handler


class FakeProvider:
    model = "fake:1b"

    def __init__(self, *, connected=True, installed=True, responses=None, forbid_probe=False):
        self.connected = connected
        self.installed = installed
        self.responses = list(responses or [])
        self.forbid_probe = forbid_probe

    def probe(self):
        if self.forbid_probe:
            raise AssertionError("fast path/dictation must not probe Ollama")
        return SimpleNamespace(
            connected=self.connected, installed=self.installed,
            error=("" if self.connected else "Ollama не запущена"),
        )

    def classify_intent(self, command, token):
        token.raise_if_cancelled()
        return "action"

    def chat(self, messages, tools, token):
        token.raise_if_cancelled()
        return self.responses.pop(0) if self.responses else {"message": {"content": "Нет действия"}}


def cfg_for(path, **overrides):
    body = dict(config.DEFAULTS, data_dir=str(path))
    body.update(overrides)
    return config.Config(body)


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.router = VoiceCommandRouter(cfg_for(tempfile.gettempdir()), AgentSession())

    def assert_fast(self, phrase, tool, arguments):
        decision = self.router.route(phrase)
        self.assertEqual(decision.kind, RouteKind.FAST_PATH)
        self.assertEqual(decision.calls, [ToolCall(tool, arguments)])

    def test_routing_dictation_vs_action(self):
        self.assertEqual(self.router.route("Сегодня я открыл новую книгу").kind,
                         RouteKind.DICTATION)
        self.assertEqual(self.router.route("Как открыть Telegram на телефоне").kind,
                         RouteKind.DICTATION)
        self.assertEqual(self.router.route("Организуй работу с окнами приложения").kind,
                         RouteKind.AGENT)
        chained = self.router.route("Открой Telegram и напиши сообщение")
        self.assertEqual(chained.kind, RouteKind.FAST_PATH)
        self.assertEqual([call.name for call in chained.calls], ["launch_app", "type_text"])

    def test_required_fast_path_grammar(self):
        self.assert_fast("Открой Telegram", "launch_app", {"app_name": "telegram"})
        self.assert_fast("Открой Chrome", "launch_app", {"app_name": "chrome"})
        self.assert_fast("Закрой Telegram", "close_app", {"app_name": "telegram"})
        self.assert_fast("Сделай громкость 30 процентов", "set_volume", {"percent": 30})
        self.assert_fast("Сделай потише", "change_volume", {"delta": -10})


class OllamaTests(unittest.TestCase):
    def test_defaults(self):
        provider = OllamaProvider(cfg_for(tempfile.gettempdir()))
        self.assertEqual(provider.base_url, "http://127.0.0.1:11434")
        self.assertEqual(provider.model, "qwen3.5:9b")

    def test_offline_diagnostic_does_not_raise(self):
        provider = OllamaProvider(cfg_for(tempfile.gettempdir()))

        class Offline:
            def open(self, request, timeout=None):
                raise urllib.error.URLError(ConnectionRefusedError())

        provider._opener = Offline()
        status = provider.probe()
        self.assertFalse(status.connected)
        self.assertFalse(status.installed)
        self.assertIn("Ollama", status.error)

    def test_model_missing_diagnostic(self):
        provider = OllamaProvider(cfg_for(tempfile.gettempdir()))
        provider._request = lambda *args, **kwargs: {"models": [{"name": "llama3:8b"}]}
        status = provider.probe()
        self.assertTrue(status.connected)
        self.assertFalse(status.installed)
        self.assertIn("не установлена", status.error)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.automation = FakeAutomation()
        self.registry = ToolRegistry(self.automation)
        self.token = CancellationToken()

    def test_invalid_tool_call(self):
        missing = self.registry.execute(ToolCall("does_not_exist", {}), self.token)
        invalid = self.registry.execute(
            ToolCall("set_volume", {"percent": 30, "command": "whoami"}), self.token)
        self.assertEqual(missing.code, ErrorCode.UNSUPPORTED_ACTION)
        self.assertEqual(invalid.code, ErrorCode.INVALID_ARGUMENT)
        self.assertEqual(self.automation.calls, [])

    def test_shell_execution_is_unavailable(self):
        forbidden = {"shell", "powershell", "cmd", "exec", "run_command", "run_process"}
        self.assertFalse(set(self.registry.names) & forbidden)
        schemas = str(self.registry.schemas()).lower()
        self.assertNotIn("powershell", schemas)
        self.assertNotIn("run_command", schemas)
        with tempfile.TemporaryDirectory() as tmp:
            resolver = AppResolver(tmp)
            self.assertIsNone(resolver.resolve("powershell"))
            self.assertIsNone(resolver.resolve("cmd"))
            self.assertIsNone(resolver.resolve("powershell -c whoami"))


class ServiceTests(unittest.TestCase):
    def test_fast_path_works_with_ollama_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            automation = FakeAutomation()
            service = DesktopAgentService(
                cfg_for(tmp), automation=automation,
                provider=FakeProvider(connected=False, forbid_probe=True))
            result = service.execute("Открой Telegram")
            self.assertTrue(result.success)
            self.assertFalse(result.used_model)
            self.assertEqual(automation.calls, [("launch_app", {"app_name": "telegram"})])

    def test_dictation_does_not_touch_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp), automation=FakeAutomation(),
                provider=FakeProvider(forbid_probe=True))
            result = service.execute("Сегодня хорошая погода")
            self.assertFalse(result.handled)
            self.assertEqual(result.status, AgentStatus.DICTATION)

    def test_ollama_offline_and_model_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            offline = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent"), automation=FakeAutomation(),
                provider=FakeProvider(connected=False))
            missing = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent"), automation=FakeAutomation(),
                provider=FakeProvider(installed=False))
            self.assertIn("Ollama", offline.execute("Сложная задача").message)
            self.assertIn("не установлена", missing.execute("Сложная задача").message)

    def test_max_agent_steps(self):
        call = {"message": {"tool_calls": [
            {"function": {"name": "get_running_apps", "arguments": {}}},
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent", max_agent_steps=2),
                automation=FakeAutomation(), provider=FakeProvider(responses=[call, call, call]))
            result = service.execute("Сложная задача")
            self.assertFalse(result.success)
            self.assertIn("лимит 2", result.message)
            self.assertEqual(len(result.steps), 2)

    def test_many_tool_calls_in_one_response_are_bounded(self):
        call = {"message": {"tool_calls": [
            {"function": {"name": "get_running_apps", "arguments": {}}}
            for _ in range(10)
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent", max_agent_steps=3),
                automation=FakeAutomation(), provider=FakeProvider(responses=[call]))
            result = service.execute("Сложная задача")
            self.assertFalse(result.success)
            self.assertEqual(len(result.steps), 3)

    def test_model_invalid_tool_call_is_structured(self):
        invalid = {"message": {"tool_calls": [
            {"function": {"name": "powershell", "arguments": {"command": "whoami"}}},
        ]}}
        finish = {"message": {"content": "Недоступно"}}
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent"), automation=FakeAutomation(),
                provider=FakeProvider(responses=[invalid, finish]))
            result = service.execute("Выполни команду оболочки")
            self.assertFalse(result.success)
            self.assertEqual(result.steps[0].code, ErrorCode.UNSUPPORTED_ACTION)

    def test_context_pronoun_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            automation = FakeAutomation()
            service = DesktopAgentService(
                cfg_for(tmp), automation=automation, provider=FakeProvider(forbid_probe=True))
            self.assertTrue(service.execute("Открой Telegram").success)
            self.assertTrue(service.execute("Закрой его").success)
            self.assertEqual(automation.calls[-1], ("close_app", {"app_name": "telegram"}))

    def test_cancellation(self):
        class BlockingProvider(FakeProvider):
            def chat(self, messages, tools, token):
                while True:
                    token.raise_if_cancelled()
                    token.wait(0.01)

        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp, voice_mode="agent"), automation=FakeAutomation(),
                provider=BlockingProvider())
            box = []
            thread = threading.Thread(target=lambda: box.append(service.execute("Сложная задача")))
            thread.start()
            deadline = time.time() + 2
            while not service.running and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(service.cancel("test stop"))
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(box[0].status, AgentStatus.CANCELLED)


class EngineIntegrationTests(unittest.TestCase):
    def test_dictation_still_reaches_existing_insert_after_routing(self):
        class DictationAgent:
            running = False

            def execute(self, command, source="voice"):
                return AgentResult(False, True, AgentStatus.DICTATION, "Диктовка", command=command)

        with tempfile.TemporaryDirectory() as tmp:
            listener = Listener(cfg_for(tmp, output_mode="insert"))
            listener._agent = DictationAgent()
            inserted = []
            listener._insert = lambda text, window: inserted.append((text, window))
            listener._handle_command(None, tail="Обычный текст", wake_window=123)
            self.assertEqual(inserted, [("Обычный текст", 123)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
