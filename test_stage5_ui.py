"""Dependency-light Stage 5 state and design-system tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from voicetool.gui import theme
from voicetool.gui.ui_state import (
    AliceState,
    EngineHealth,
    engine_health,
    normalize_state,
    presentation,
    recent_agent_activity,
)


class AlicePresentationTests(unittest.TestCase):
    def test_all_required_states_have_presentations(self):
        required = {
            "idle", "listening", "processing", "executing",
            "waiting_confirmation", "success", "error", "cancelled",
        }
        self.assertEqual({state.value for state in AliceState}, required)
        for state in AliceState:
            view = presentation(state)
            self.assertTrue(view.title)
            self.assertTrue(view.detail)

    def test_voice_and_agent_states_share_one_mapping(self):
        self.assertEqual(normalize_state("wake"), AliceState.LISTENING)
        self.assertEqual(normalize_state("recording"), AliceState.LISTENING)
        self.assertEqual(normalize_state("understanding"), AliceState.PROCESSING)
        self.assertEqual(normalize_state("waiting_confirmation"),
                         AliceState.WAITING_CONFIRMATION)


class DesignSystemTests(unittest.TestCase):
    def test_semantic_palette_spacing_radius_and_motion_are_centralized(self):
        palette = theme.PALETTE
        for name in (
            "background_primary", "background_secondary", "surface_primary",
            "surface_secondary", "surface_hover", "surface_active", "border_subtle",
            "border_focus", "text_primary", "text_secondary", "text_muted",
            "accent_primary", "accent_hover", "accent_glow", "success", "warning",
            "danger",
        ):
            self.assertTrue(getattr(palette, name))
        self.assertEqual(sorted(theme.SPACING.values()), [4, 8, 12, 16, 20, 24, 32, 40, 48])
        self.assertEqual(set(theme.RADIUS_SCALE), {"small", "medium", "large"})
        self.assertLess(theme.MOTION["micro"], theme.MOTION["regular"])
        self.assertLess(theme.MOTION["regular"], theme.MOTION["large"])


class EngineHealthTests(unittest.TestCase):
    def test_real_ollama_states(self):
        self.assertEqual(engine_health(SimpleNamespace(connected=False, installed=False)),
                         EngineHealth.OFFLINE)
        self.assertEqual(engine_health(SimpleNamespace(connected=True, installed=False)),
                         EngineHealth.MODEL_MISSING)
        self.assertEqual(engine_health(SimpleNamespace(connected=True, installed=True)),
                         EngineHealth.READY)
        self.assertEqual(engine_health(None), EngineHealth.ERROR)
        self.assertEqual(engine_health(None, downloading=True), EngineHealth.DOWNLOADING)


class ActivityPrivacyTests(unittest.TestCase):
    def test_activity_rows_are_human_readable_and_hide_raw_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_activity.jsonl"
            path.write_text(json.dumps({
                "timestamp": "2026-08-31T07:30:00+00:00",
                "user_command": "Открой Telegram",
                "tool": "launch_app",
                "safe_arguments": {"app_name": "Telegram", "secret": "[redacted]"},
                "result": {"success": True, "message": "Telegram открыт"},
                "raw_model_prompt": "must never be exposed",
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            rows = recent_agent_activity(tmp)
        self.assertEqual(rows[0]["command"], "Открой Telegram")
        self.assertEqual(rows[0]["target"], "Telegram")
        self.assertEqual(rows[0]["result"], "Telegram открыт")
        rendered = json.dumps(rows, ensure_ascii=False)
        self.assertNotIn("raw_model_prompt", rendered)
        self.assertNotIn("must never be exposed", rendered)
        self.assertNotIn("secret", rendered)


if __name__ == "__main__":
    unittest.main()
