"""Pure/abstracted tests for Stage 2 Windows-native tools (safe on Linux CI)."""
import tempfile
import time
import unittest
from pathlib import Path

from voicetool import config
from voicetool.agent.cancellation import CancellationToken
from voicetool.agent.desktop import (AppCandidate, AppResolver, DesktopController,
                                     WindowInfo, validate_url)
from voicetool.agent.router import RouteKind, VoiceCommandRouter
from voicetool.agent.session import AgentSession
from voicetool.agent.tools import ToolRegistry
from voicetool.agent.types import ErrorCode, ToolCall


def cfg_for(path, **overrides):
    body = dict(config.DEFAULTS, data_dir=str(path), app_launch_timeout_seconds=0)
    body.update(overrides)
    return config.Config(body)


class FakeResolver:
    def __init__(self, candidates=()):
        self.items = list(candidates)

    def candidates(self, refresh=False):
        return list(self.items)

    def resolve(self, query):
        wanted = str(query).casefold()
        return next((item for item in self.items
                     if wanted in item.name.casefold() or wanted in item.process_name.casefold()), None)


class FakeWindows:
    def __init__(self, windows=()):
        self.items = list(windows)
        self.focused = []
        self.shown = []
        self.closed = []

    def list(self):
        return list(self.items)

    def find(self, query):
        wanted = str(query).casefold()
        return next((item for item in self.items
                     if wanted in item.title.casefold() or wanted in item.process.casefold()), None)

    def active(self):
        return self.items[0] if self.items else None

    def focus(self, window):
        self.focused.append(window.hwnd)
        return True

    def show(self, window, command):
        self.shown.append((window.hwnd, command))
        return True

    def close(self, window):
        self.closed.append(window.hwnd)
        self.items = [item for item in self.items if item.hwnd != window.hwnd]
        return True


class FakeInput:
    def __init__(self):
        self.texts = []
        self.keys = []

    def type_text(self, text, **kwargs):
        self.texts.append((text, kwargs))
        return True

    def press_keys(self, keys):
        self.keys.append(list(keys))
        return True


class FakeEndpoint:
    def __init__(self, value=0.5, muted=False):
        self.value = value
        self.muted = muted

    def GetMasterVolumeLevelScalar(self):
        return self.value

    def SetMasterVolumeLevelScalar(self, value, _):
        self.value = value

    def GetMute(self):
        return self.muted

    def SetMute(self, value, _):
        self.muted = bool(value)


class RouterStage2Tests(unittest.TestCase):
    def setUp(self):
        self.router = VoiceCommandRouter(cfg_for(tempfile.gettempdir()), AgentSession())

    def assert_tools(self, phrase, *names):
        decision = self.router.route(phrase)
        self.assertEqual(decision.kind, RouteKind.FAST_PATH, phrase)
        self.assertEqual([call.name for call in decision.calls], list(names))
        return decision.calls

    def test_required_native_fast_paths(self):
        self.assert_tools("Открой Telegram", "launch_app")
        self.assert_tools("Открой Chrome", "launch_app")
        self.assert_tools("Закрой Telegram", "close_app")
        self.assert_tools("Переключись на Chrome", "focus_app")
        self.assert_tools("Сверни Chrome", "minimize_window")
        youtube = self.assert_tools("Открой YouTube", "open_url")
        self.assertEqual(youtube[0].arguments["url"], "https://www.youtube.com")
        chain = self.assert_tools("Открой блокнот и напиши привет", "launch_app", "type_text")
        self.assertEqual(chain[1].arguments["text"], "привет")
        self.assert_tools("Громкость 30 процентов", "set_volume")
        self.assert_tools("Выключи звук", "mute_volume")
        downloads = self.assert_tools("Открой Downloads", "open_folder")
        self.assertEqual(downloads[0].arguments["path"], "downloads")


class AppResolverTests(unittest.TestCase):
    def test_fuzzy_builtin_names_and_dota_are_data_driven(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolver = AppResolver(tmp)
            resolver._cache = [
                AppCandidate("Telegram Desktop", r"C:\Apps\Telegram.exe", "start_menu", "Telegram.exe"),
                AppCandidate("Google Chrome", r"C:\Apps\chrome.exe", "registry", "chrome.exe"),
                AppCandidate("Discord", r"C:\Apps\Discord.exe", "running", "Discord.exe"),
                AppCandidate("Dota 2", r"C:\Games\Dota 2.lnk", "start_menu"),
            ]
            resolver._cache_at = time.time()
            self.assertEqual(resolver.resolve("телега").name, "Telegram Desktop")
            self.assertEqual(resolver.resolve("телеграм").name, "Telegram Desktop")
            self.assertEqual(resolver.resolve("chrome").name, "Google Chrome")
            self.assertEqual(resolver.resolve("дискорд").name, "Discord")
            self.assertEqual(resolver.resolve("дота").name, "Dota 2")

    def test_persistent_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "CompanyChat.exe"
            target.write_bytes(b"")
            resolver = AppResolver(tmp)
            resolver.set_alias("рабочий чат", str(target))
            reloaded = AppResolver(tmp)
            self.assertEqual(reloaded.resolve("рабочий чат").target, str(target))
            self.assertTrue(reloaded.remove_alias("рабочий чат"))
            self.assertEqual(reloaded.aliases(), {})

    def test_discovered_candidate_beats_fallback_and_shell_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolver = AppResolver(tmp)
            resolver._cache = [
                AppCandidate("telegram", "Telegram.exe", "known", "Telegram.exe"),
                AppCandidate("Telegram Desktop", r"C:\Start\Telegram.lnk", "start_menu"),
            ]
            resolver._cache_at = time.time()
            self.assertEqual(resolver.resolve("telegram").source, "start_menu")
            self.assertIsNone(resolver.resolve("powershell -c whoami"))
            shell = Path(tmp) / "powershell.exe"
            shell.write_bytes(b"")
            resolver.set_alias("рабочая программа", str(shell))
            self.assertIsNone(resolver.resolve("рабочая программа"))


class NativeToolTests(unittest.TestCase):
    def test_existing_app_is_focused_without_duplicate(self):
        window = WindowInfo(10, "Telegram", 1, "Telegram.exe")
        windows = FakeWindows([window])
        starts = []
        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(
                cfg_for(tmp), resolver=FakeResolver(), windows=windows, starter=starts.append)
            result = controller.launch_app("Telegram", CancellationToken())
        self.assertTrue(result.success)
        self.assertTrue(result.data["existing_instance"])
        self.assertEqual(windows.focused, [10])
        self.assertEqual(starts, [])

    def test_launch_is_verified_and_nonexistent_app_is_structured(self):
        candidate = AppCandidate("Telegram", r"C:\Apps\Telegram.exe", "registry", "Telegram.exe")
        windows = FakeWindows()

        def start(_):
            windows.items.append(WindowInfo(11, "Telegram", 2, "Telegram.exe"))

        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(
                cfg_for(tmp), resolver=FakeResolver([candidate]), windows=windows, starter=start)
            launched = controller.launch_app("Telegram", CancellationToken())
            missing = controller.launch_app("Missing", CancellationToken())
        self.assertTrue(launched.success)
        self.assertTrue(launched.data["verified"])
        self.assertTrue(launched.data["focused"])
        self.assertEqual(windows.focused, [11])
        self.assertEqual(missing.code, ErrorCode.APP_NOT_FOUND)

    def test_window_actions_and_missing_window(self):
        window = WindowInfo(22, "Google Chrome", 2, "chrome.exe")
        windows = FakeWindows([window])
        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(cfg_for(tmp), resolver=FakeResolver(), windows=windows)
            self.assertTrue(controller.focus_app("Chrome").success)
            self.assertTrue(controller.minimize_window("Chrome").success)
            self.assertTrue(controller.maximize_window("Chrome").success)
            self.assertTrue(controller.restore_window("Chrome").success)
            self.assertEqual(controller.focus_window("Missing").code, ErrorCode.WINDOW_NOT_FOUND)
            self.assertTrue(controller.close_window("Chrome").success)

    def test_url_scheme_policy(self):
        opened = []

        def opener(url, new=0):
            opened.append((url, new))
            return True

        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(cfg_for(tmp), browser_opener=opener)
            self.assertTrue(controller.open_url("https://youtube.com").success)
            self.assertEqual(controller.open_url("file:///etc/passwd").code,
                             ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(controller.open_url("javascript:alert(1)").code,
                             ErrorCode.INVALID_ARGUMENT)
            allowed = DesktopController(
                cfg_for(tmp, allowed_url_schemes=["spotify"]), browser_opener=opener)
            self.assertTrue(allowed.open_url("spotify:track:123").success)
        self.assertEqual(opened[0][0], "https://youtube.com")
        with self.assertRaises(ValueError):
            validate_url("https://user:password@example.com")

    def test_unicode_typing_abstraction_and_shortcuts(self):
        backend = FakeInput()
        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(cfg_for(tmp), input_backend=backend)
            result = controller.type_text("Привет, мир 👋")
            shortcut = controller.press_keys(["ctrl", "l"])
            submit = controller.press_key("enter")
        self.assertTrue(result.success)
        self.assertTrue(result.data["unicode"])
        self.assertEqual(backend.texts[0][0], "Привет, мир 👋")
        self.assertTrue(shortcut.success)
        self.assertEqual(backend.keys, [["ctrl", "l"]])
        self.assertEqual(submit.code, ErrorCode.INVALID_ARGUMENT)

    def test_volume_boundaries_and_mute(self):
        endpoint = FakeEndpoint()
        with tempfile.TemporaryDirectory() as tmp:
            controller = DesktopController(
                cfg_for(tmp), volume_endpoint_factory=lambda: endpoint)
            self.assertTrue(controller.set_volume(0).success)
            self.assertTrue(controller.set_volume(100).success)
            self.assertEqual(controller.set_volume(-1).code, ErrorCode.INVALID_ARGUMENT)
            self.assertEqual(controller.set_volume(101).code, ErrorCode.INVALID_ARGUMENT)
            self.assertTrue(controller.change_volume(10).success)
            self.assertEqual(controller.get_volume().data["percent"], 100)
            self.assertTrue(controller.mute_volume().data["muted"])
            self.assertFalse(controller.unmute_volume().data["muted"])

    def test_safe_file_tools(self):
        opened = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "отчёт.txt"
            file.write_text("данные", encoding="utf-8")
            controller = DesktopController(cfg_for(tmp), starter=opened.append)
            found = controller.find_file("отч", CancellationToken(), root=tmp)
            self.assertTrue(found.success)
            self.assertEqual(found.data["path"], str(file))
            self.assertTrue(controller.open_file(str(file)).success)
            self.assertTrue(controller.open_folder(tmp).success)
            self.assertEqual(controller.open_file(str(root / "missing.txt")).code,
                             ErrorCode.FILE_NOT_FOUND)
        self.assertEqual(opened, [str(file), tmp])


class RegistryStage2Tests(unittest.TestCase):
    def test_registry_contains_native_tools_only(self):
        registry = ToolRegistry(type("Controller", (), {name: (lambda self, **kwargs: None)
            for name in (
                "resolve_app", "list_installed_apps", "launch_app", "focus_app", "close_app",
                "get_running_apps", "list_windows", "get_active_window", "focus_window",
                "minimize_window", "maximize_window", "restore_window", "close_window",
                "open_url", "type_text", "press_key", "press_keys", "get_volume",
                "set_volume", "change_volume", "mute_volume", "unmute_volume",
                "open_file", "open_folder", "find_file", "show_in_folder",
            )})())
        required = {
            "resolve_app", "list_installed_apps", "launch_app", "focus_app", "close_app",
            "get_running_apps", "list_windows", "get_active_window", "focus_window",
            "minimize_window", "maximize_window", "restore_window", "close_window",
            "open_url", "type_text", "press_key", "press_keys", "get_volume",
            "set_volume", "change_volume", "mute_volume", "unmute_volume",
            "open_file", "open_folder", "find_file", "show_in_folder",
        }
        self.assertEqual(set(registry.names), required)
        forbidden = {"mouse_click", "take_screenshot", "launch_steam_game",
                     "invoke_ui_element", "shell", "powershell", "run_command"}
        self.assertFalse(set(registry.names) & forbidden)

    def test_invalid_arguments_are_structured(self):
        class Controller:
            def __getattr__(self, name):
                return lambda **kwargs: None

        registry = ToolRegistry(Controller())
        result = registry.execute(ToolCall("set_volume", {"percent": 101}), CancellationToken())
        self.assertEqual(result.code, ErrorCode.INVALID_ARGUMENT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
