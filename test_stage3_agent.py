"""Dependency-light Stage 3 tests for Steam, multi-step safety and context."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from voicetool import config
from voicetool.agent.action_log import ActivityLogger
from voicetool.agent.cancellation import CancellationToken
from voicetool.agent.router import RouteKind, VoiceCommandRouter
from voicetool.agent.safety import ConfirmationPolicy, PROMPT_INJECTION_BOUNDARY, Risk
from voicetool.agent.service import DesktopAgentService
from voicetool.agent.session import AgentSession
from voicetool.agent.steam import SteamController, parse_app_manifest, parse_vdf
from voicetool.agent.tools import ToolRegistry
from voicetool.agent.types import AgentStatus, ErrorCode, ToolCall, ToolResult


def cfg_for(path, **overrides):
    body = dict(config.DEFAULTS, data_dir=str(path), steam_launch_timeout_seconds=0)
    body.update(overrides)
    return config.Config(body)


def tool_response(name, arguments):
    return {"message": {"content": "", "tool_calls": [{
        "id": name, "function": {"name": name, "arguments": arguments},
    }]}}


def finish_response(text="Готово"):
    return {"message": {"content": text}}


class FakeProvider:
    model = "fake:1b"

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    def probe(self):
        return SimpleNamespace(connected=True, installed=True, error="")

    def classify_intent(self, command, token):
        token.raise_if_cancelled()
        return "action"

    def chat(self, messages, tools, token):
        token.raise_if_cancelled()
        self.requests.append({"messages": [dict(item) for item in messages], "tools": tools})
        return self.responses.pop(0) if self.responses else finish_response()


class FakeAutomation:
    def __init__(self, fail_once=()):
        self.calls = []
        self.fail_once = set(fail_once)

    def __getattr__(self, name):
        def handler(**arguments):
            arguments.pop("token", None)
            self.calls.append((name, dict(arguments)))
            signature = (name, arguments.get("app_name", ""))
            if signature in self.fail_once:
                self.fail_once.remove(signature)
                return ToolResult.fail(ErrorCode.APP_NOT_FOUND, "Приложение не найдено")
            data = dict(arguments)
            if "app_name" in arguments:
                data["app"] = arguments["app_name"]
            if "window" in arguments:
                data["title"] = arguments["window"]
            return ToolResult.ok(f"{name} ok", data=data)
        return handler


class FakeSteam:
    def __init__(self):
        self.calls = []

    def get_installed(self):
        return ToolResult.ok(data={"games": []})

    def resolve_game(self, game_name):
        return ToolResult.ok(data={
            "app_id": 570, "name": "Dota 2", "install_dir": "dota 2 beta",
            "install_path": "C:/Steam/steamapps/common/dota 2 beta",
            "library_path": "C:/Steam", "manifest_path": "appmanifest_570.acf",
            "state_flags": 4, "installed": True,
        })

    def launch_game(self, game_name, token=None):
        if token:
            token.raise_if_cancelled()
        self.calls.append(game_name)
        return ToolResult.ok("Dota 2 запущена", data={
            "name": "Dota 2", "app_id": 570, "verified": True,
        })


def build_steam_tree(root: Path):
    steam = root / "Steam"
    library = root / "Games"
    (steam / "steamapps").mkdir(parents=True)
    common = library / "steamapps" / "common" / "dota 2 beta"
    common.mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n"0" { "path" "' + str(steam) + '" }\n'
        '"1" { "path" "' + str(library) + '" }\n}\n',
        encoding="utf-8",
    )
    manifest = library / "steamapps" / "appmanifest_570.acf"
    manifest.write_text(
        '"AppState"\n{\n"appid" "570"\n"name" "Dota 2"\n'
        '"installdir" "dota 2 beta"\n"StateFlags" "4"\n}\n',
        encoding="utf-8",
    )
    (library / "steamapps" / "appmanifest_999.acf").write_text(
        '"AppState" { "appid" "999" "name" "Pending" '
        '"installdir" "pending" "StateFlags" "2" }',
        encoding="utf-8",
    )
    return steam, library, manifest, common


class SteamTests(unittest.TestCase):
    def test_manifest_parser_and_install_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam, library, manifest, _ = build_steam_tree(Path(tmp))
            parsed = parse_app_manifest(manifest, library)
            self.assertEqual(parsed.app_id, 570)
            self.assertEqual(parsed.name, "Dota 2")
            self.assertTrue(parsed.installed)
            self.assertEqual(parse_vdf('"root" { "key" "value" }')["root"]["key"], "value")
            controller = SteamController(steam_path=steam)
            self.assertEqual([game.app_id for game in controller.installed_games()], [570])

    def test_dota_alias_and_game_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam, _, _, _ = build_steam_tree(Path(tmp))
            controller = SteamController(steam_path=steam)
            self.assertEqual(controller.resolve("дота").app_id, 570)
            self.assertEqual(controller.resolve("Dota").name, "Dota 2")
            self.assertEqual(controller.resolve_game("дота").data["app_id"], 570)
            self.assertEqual(controller.resolve_game("missing").code,
                             ErrorCode.STEAM_GAME_NOT_FOUND)

    def test_direct_launch_uri_and_process_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            steam, _, _, common = build_steam_tree(Path(tmp))
            processes = []
            launched = []

            def starter(uri):
                launched.append(uri)
                processes.append({"exe": str(common / "game.exe"), "cmdline": []})

            controller = SteamController(
                cfg_for(tmp), steam_path=steam, starter=starter,
                process_iter=lambda: processes,
            )
            result = controller.launch_game("Dota", CancellationToken())
            self.assertTrue(result.success)
            self.assertTrue(result.data["verified"])
            self.assertEqual(launched, ["steam://run/570"])


class RouterAndSessionTests(unittest.TestCase):
    def test_dota_and_required_multistep_plans(self):
        router = VoiceCommandRouter(cfg_for(tempfile.gettempdir()), AgentSession())
        dota = router.route("Алиса, открой Dota")
        self.assertEqual(dota.kind, RouteKind.FAST_PATH)
        self.assertEqual(dota.calls, [ToolCall("launch_steam_game", {"game_name": "Dota 2"})])
        cases = {
            "Открой Chrome, потом Telegram и вернись в Chrome":
                ["launch_app", "launch_app", "focus_app"],
            "Запусти Steam, Dota и сверни Steam":
                ["launch_app", "launch_steam_game", "minimize_window"],
            "Открой блокнот и напиши список покупок":
                ["launch_app", "type_text"],
        }
        for phrase, expected in cases.items():
            with self.subTest(phrase=phrase):
                decision = router.route(phrase)
                self.assertEqual(decision.kind, RouteKind.FAST_PATH)
                self.assertEqual([call.name for call in decision.calls], expected)

    def test_short_term_references_and_targets(self):
        session = AgentSession()
        session.remember_tool(
            "launch_steam_game", {"game_name": "Dota 2"},
            ToolResult.ok(data={"name": "Dota 2", "app_id": 570}),
        )
        router = VoiceCommandRouter(cfg_for(tempfile.gettempdir()), session)
        self.assertEqual(router.route("Сверни её").calls[0].arguments["window"], "Dota 2")
        session.remember_tool(
            "focus_app", {"app_name": "Telegram"},
            ToolResult.ok(data={"app": "Telegram"}),
        )
        self.assertEqual(router.route("Вернись туда").calls[0].arguments["app_name"], "Telegram")
        context = session.model_context()
        self.assertEqual(context["last_steam_game"], "Dota 2")
        self.assertEqual(context["last_active_app"], "Telegram")
        self.assertTrue(context["last_tool_targets"])


class AgentLoopTests(unittest.TestCase):
    def test_fast_multistep_success_without_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            automation = FakeAutomation()
            steam = FakeSteam()
            provider = FakeProvider()
            service = DesktopAgentService(
                cfg_for(tmp), automation=automation, steam=steam, provider=provider)
            result = service.execute("Открой Chrome, потом Telegram и вернись в Chrome")
        self.assertTrue(result.success)
        self.assertEqual([step.tool for step in result.steps],
                         ["launch_app", "launch_app", "focus_app"])
        self.assertEqual(provider.requests, [])

    def test_model_receives_each_result_and_recovers_after_failure(self):
        responses = [
            tool_response("launch_app", {"app_name": "Missing"}),
            tool_response("launch_app", {"app_name": "Chrome"}),
            finish_response("Chrome открыт"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            automation = FakeAutomation({("launch_app", "Missing")})
            provider = FakeProvider(responses)
            service = DesktopAgentService(
                cfg_for(tmp), automation=automation, steam=FakeSteam(), provider=provider)
            result = service.execute("Организуй окна приложений")
        self.assertTrue(result.success)
        self.assertFalse(result.steps[0].success)
        self.assertTrue(result.steps[1].success)
        second_messages = provider.requests[1]["messages"]
        self.assertTrue(any(
            item.get("role") == "tool" and '"success":false' in item.get("content", "")
            for item in second_messages
        ))

    def test_max_steps_is_preserved(self):
        response = {"message": {"tool_calls": [
            {"function": {"name": "launch_app", "arguments": {"app_name": name}}}
            for name in ("one", "two", "three")
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            service = DesktopAgentService(
                cfg_for(tmp, max_agent_steps=2), automation=FakeAutomation(),
                steam=FakeSteam(), provider=FakeProvider([response]))
            result = service.execute("Организуй окна приложений")
        self.assertFalse(result.success)
        self.assertEqual(len(result.steps), 2)
        self.assertIn("лимит 2", result.message)

    def test_cancellation_stops_wait_and_following_action(self):
        response = {"message": {"tool_calls": [
            {"function": {"name": "wait", "arguments": {"seconds": 2}}},
            {"function": {"name": "launch_app", "arguments": {"app_name": "Chrome"}}},
        ]}}
        with tempfile.TemporaryDirectory() as tmp:
            automation = FakeAutomation()
            service = DesktopAgentService(
                cfg_for(tmp), automation=automation, steam=FakeSteam(),
                provider=FakeProvider([response]))
            box = {}
            thread = threading.Thread(
                target=lambda: box.setdefault("result", service.execute(
                    "Организуй окна приложений")))
            thread.start()
            deadline = time.time() + 2
            while not service.running and time.time() < deadline:
                time.sleep(0.01)
            time.sleep(0.05)
            self.assertTrue(service.cancel("test cancellation"))
            thread.join(2)
        self.assertEqual(box["result"].status, AgentStatus.CANCELLED)
        self.assertFalse(any(name == "launch_app" for name, _ in automation.calls))


class ConfirmationTests(unittest.TestCase):
    def test_policy_requires_only_high_impact(self):
        policy = ConfirmationPolicy()
        for tool in ("launch_app", "focus_window", "set_volume", "open_url",
                     "open_file", "type_text", "launch_steam_game"):
            self.assertEqual(policy.classify(tool, {})[0], Risk.LOW)
        self.assertTrue(policy.needs_confirmation("delete_file", {"confirmed": True})[0])
        self.assertTrue(policy.needs_confirmation("publish", {})[0])
        self.assertTrue(policy.needs_confirmation(
            "press_key", {"key": "enter", "confirmed": True})[0])

    def test_confirmation_cancel_and_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            registry = ToolRegistry(
                FakeAutomation(), steam=FakeSteam(),
                activity_logger=ActivityLogger(tmp))
            registry._add(
                "delete_file", "test-only high-impact tool",
                {"path": {"type": "string"}}, ("path",),
                lambda path: calls.append(path) or ToolResult.ok(),
            )
            token = CancellationToken()
            rejected = registry.execute(
                ToolCall("delete_file", {"path": "a.txt"}), token,
                confirm=lambda request: False,
            )
            accepted = registry.execute(
                ToolCall("delete_file", {"path": "b.txt"}), token,
                confirm=lambda request: True,
            )
        self.assertEqual(rejected.code, ErrorCode.CONFIRMATION_REJECTED)
        self.assertTrue(accepted.success)
        self.assertEqual(calls, ["b.txt"])

    def test_service_waiting_confirmation_reject_and_approve(self):
        def run(approved):
            events, requests, calls = [], [], []
            provider = FakeProvider([
                tool_response("send_message", {"recipient": "team"}),
                finish_response("done"),
            ])
            with tempfile.TemporaryDirectory() as tmp:
                service = DesktopAgentService(
                    cfg_for(tmp), automation=FakeAutomation(), steam=FakeSteam(),
                    provider=provider, events={
                        "event": events.append,
                        "confirmation": requests.append,
                    })
                service.registry._add(
                    "send_message", "test-only high-impact tool",
                    {"recipient": {"type": "string"}}, ("recipient",),
                    lambda recipient: calls.append(recipient) or ToolResult.ok("sent"),
                )
                box = {}
                thread = threading.Thread(
                    target=lambda: box.setdefault(
                        "result", service.execute("Организуй окна приложений")))
                thread.start()
                deadline = time.time() + 2
                while service.pending_confirmation is None and time.time() < deadline:
                    time.sleep(0.01)
                self.assertIsNotNone(service.pending_confirmation)
                self.assertTrue(any(
                    event.status == AgentStatus.WAITING_CONFIRMATION for event in events))
                self.assertTrue(service.resolve_confirmation(approved))
                thread.join(2)
                return box["result"], calls

        rejected, rejected_calls = run(False)
        accepted, accepted_calls = run(True)
        self.assertFalse(rejected.success)
        self.assertEqual(rejected_calls, [])
        self.assertTrue(accepted.success)
        self.assertEqual(accepted_calls, ["team"])

    def test_cancellation_stops_pending_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            service = DesktopAgentService(
                cfg_for(tmp), automation=FakeAutomation(), steam=FakeSteam(),
                provider=FakeProvider([tool_response(
                    "publish", {"target": "release"})]),
                events={"confirmation": lambda request: None},
            )
            service.registry._add(
                "publish", "test-only high-impact tool",
                {"target": {"type": "string"}}, ("target",),
                lambda target: calls.append(target) or ToolResult.ok(),
            )
            box = {}
            thread = threading.Thread(target=lambda: box.setdefault(
                "result", service.execute("Организуй окна приложений")))
            thread.start()
            deadline = time.time() + 2
            while service.pending_confirmation is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(service.pending_confirmation)
            self.assertTrue(service.cancel("cancel pending confirmation"))
            thread.join(2)
            activity = Path(tmp, "agent_activity.jsonl").read_text(encoding="utf-8")
        self.assertEqual(box["result"].status, AgentStatus.CANCELLED)
        self.assertEqual(calls, [])
        self.assertIn('"confirmation":"cancelled"', activity)


class ActivityAndBoundaryTests(unittest.TestCase):
    def test_activity_log_is_structured_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger = ActivityLogger(tmp)
            registry = ToolRegistry(
                FakeAutomation(), steam=FakeSteam(), activity_logger=logger)
            result = registry.execute(
                ToolCall("type_text", {"text": "password hunter2"}),
                CancellationToken(),
                command="Напиши password hunter2",
            )
            self.assertTrue(result.success)
            body = Path(tmp, "agent_activity.jsonl").read_text(encoding="utf-8")
            entry = json.loads(body)
        self.assertNotIn("hunter2", body)
        self.assertEqual(entry["tool"], "type_text")
        self.assertEqual(entry["safe_arguments"]["text"]["characters"], 16)
        self.assertIn("timestamp", entry)
        self.assertIn("duration_ms", entry)
        self.assertEqual(entry["confirmation"], "not_required")

    def test_prompt_injection_boundary_and_registry_scope(self):
        self.assertIn("untrusted observed content", PROMPT_INJECTION_BOUNDARY)
        registry = ToolRegistry(FakeAutomation(), steam=FakeSteam())
        forbidden = {"take_screenshot", "mouse_click", "invoke_ui_element",
                     "shell", "powershell", "run_command"}
        self.assertFalse(set(registry.names) & forbidden)
        self.assertIn("resolve_steam_game", registry.names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
