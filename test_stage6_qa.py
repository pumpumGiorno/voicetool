"""Dependency-light Stage 6 integration, privacy and lifecycle regression tests."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from voicetool import config
from voicetool.agent.action_log import ActivityLogger
from voicetool.agent.cancellation import CancellationToken
from voicetool.agent.desktop import DesktopController
from voicetool.agent.safety import ConfirmationPolicy
from voicetool.agent.types import ConfirmationRequest, ErrorCode, ToolResult
from voicetool.engine import Listener
from voicetool.history import History
from voicetool.processor import BatchProcessor


def cfg_for(path, **overrides):
    body = dict(config.DEFAULTS, data_dir=str(path))
    body.update(overrides)
    return config.Config(body)


class Stage6PrivacyAndRetentionTests(unittest.TestCase):
    def test_transcript_opt_out_creates_no_history_or_activity(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            history = History(path, enabled=False)
            activity = ActivityLogger(path, enabled=False)
            history.add("private transcript")
            activity.write("private command", "type_text", {"text": "secret"},
                           ToolResult.ok("done"))
            self.assertFalse((path / "history.jsonl").exists())
            self.assertFalse((path / "agent_activity.jsonl").exists())

    def test_history_and_activity_have_line_retention(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            history = History(path, max_lines=10, max_bytes=4096)
            activity = ActivityLogger(path, max_lines=10, max_bytes=4096)
            for index in range(35):
                history.add(f"line {index}")
                activity.write(f"command {index}", "get_volume", {}, ToolResult.ok())
            self.assertLessEqual(len((path / "history.jsonl").read_text().splitlines()), 10)
            self.assertLessEqual(
                len((path / "agent_activity.jsonl").read_text().splitlines()), 10)


class Stage6SecurityTests(unittest.TestCase):
    def test_open_file_refuses_executables_and_scripts(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            opened = []
            controller = DesktopController(cfg_for(path), starter=opened.append)
            for suffix in (".exe", ".bat", ".cmd", ".ps1", ".py", ".lnk", ".url"):
                target = path / f"payload{suffix}"
                target.write_text("payload")
                result = controller.open_file(str(target))
                self.assertFalse(result.success, suffix)
                self.assertEqual(result.code, ErrorCode.ACCESS_DENIED)
            document = path / "notes.txt"
            document.write_text("hello")
            self.assertTrue(controller.open_file(str(document)).success)
            self.assertEqual(opened, [str(document)])

    def test_confirmation_keeps_original_command_and_cannot_be_weakened(self):
        request = ConfirmationRequest(
            "visual_interact", {"target": "button"}, "Confirm",
            user_command="Опубликуй это",
        )
        self.assertEqual(request.user_command, "Опубликуй это")
        required, _ = ConfirmationPolicy().needs_confirmation(
            "visual_interact", {"target": "button"},
            user_command=request.user_command,
        )
        self.assertTrue(required)

    def test_agent_surface_still_has_no_shell_tool(self):
        source = (Path(__file__).parent / "voicetool/agent/tools.py").read_text()
        for forbidden in ('_add("shell"', '_add("powershell"', '_add("cmd"'):
            self.assertNotIn(forbidden, source)


class Stage6LifecycleTests(unittest.TestCase):
    def test_listener_stop_waits_for_worker(self):
        with tempfile.TemporaryDirectory() as root:
            listener = Listener(cfg_for(root))

            def worker():
                while not listener._stop.wait(0.01):
                    pass

            listener._thread = threading.Thread(target=worker)
            listener._thread.start()
            self.assertTrue(listener.stop(wait=1.0))
            self.assertFalse(listener.running)

    def test_batch_cancel_waits_for_worker(self):
        entered = threading.Event()

        class BlockingASR:
            def transcribe_file(self, path, **kwargs):
                entered.set()
                while not kwargs["should_stop"]():
                    time.sleep(0.01)
                return {"language": "ru", "duration": 0, "text": "", "segments": []}

        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            audio = path / "audio.wav"
            audio.write_bytes(b"RIFF")
            processor = BatchProcessor(
                cfg_for(path, log_transcripts=False), asr_factory=BlockingASR)
            processor.add([audio])
            processor.start()
            self.assertTrue(entered.wait(1.0))
            self.assertTrue(processor.cancel(wait=1.0))
            self.assertFalse(processor.running)


class Stage6CompatibilityAndPipelineTests(unittest.TestCase):
    def test_legacy_reduced_motion_config_is_migrated(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            path.write_text(json.dumps({"reduce_motion": True}), encoding="utf-8")
            loaded = config.load(path)
            self.assertTrue(loaded.reduce_animations)
            self.assertIn("shutdown_timeout_seconds", loaded)

    def test_cli_uses_shared_listener_and_batch_pipeline(self):
        source = (Path(__file__).parent / "voice_tool.py").read_text(encoding="utf-8")
        self.assertIn("from voicetool.engine import Listener", source)
        self.assertIn("from voicetool.processor import DONE, BatchProcessor", source)
        self.assertNotIn("from voicetool.asr import ASR", source)
        self.assertNotIn("from voicetool.audio import Recorder", source)

    def test_transcript_opt_out_redacts_voice_diagnostics(self):
        source = (Path(__file__).parent / "voicetool/engine.py").read_text(encoding="utf-8")
        self.assertIn('if self.cfg.get("log_transcripts", True):', source)
        self.assertIn("Слово-триггер распознан; текст не сохранён", source)
        self.assertIn("Wake-фраза не совпала (%d символов)", source)

    def test_idle_overlay_has_no_continuous_repaint_and_reduced_motion_is_direct(self):
        source = (Path(__file__).parent / "voicetool/gui/voice_widget.py").read_text(
            encoding="utf-8")
        self.assertIn("state in ANIMATED_STATES", source)
        self.assertIn("state not in ANIMATED_STATES", source)
        self.assertIn("def set_reduce_motion", source)
        self.assertIn("if self._reduce_motion:\n            self._fade.stop()", source)

    def test_generated_package_is_not_stored_in_python_package(self):
        package = Path(__file__).parent / "voicetool"
        self.assertFalse((package / "VoiceTool.exe").exists())
        self.assertFalse((package / "_internal").exists())
        ignored = (Path(__file__).parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/voicetool/_internal/", ignored)
        self.assertIn("/voicetool/VoiceTool.exe", ignored)


if __name__ == "__main__":
    unittest.main()
