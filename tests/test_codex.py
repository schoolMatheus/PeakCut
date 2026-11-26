"""Codex subscription integration. CLI responses are replaced; no account or network is used."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from studio import codex
except ImportError:
    codex = None


def events(text='{"items":["ok"]}', usage=None):
    return "\n".join(json.dumps(e) for e in [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Working", "phase": "commentary"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": usage or {"input_tokens": 20, "cached_input_tokens": 12, "output_tokens": 3}},
    ])


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(codex, "The Python Codex adapter must exist")
        self.command = patch.object(codex, "command", return_value=["codex.exe"]).start()
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "api-secret", "CODEX_API_KEY": "other-secret",
                                          "OPENAI_BASE_URL": "https://other.example"}).start()
        self.addCleanup(patch.stopall)
        self.calls = []
        self.output, self.exitcode = events(), 0
        self.login = "Logged in using ChatGPT"
        self.inspect = lambda args, kw: None
        self.proc = patch.object(codex.subprocess, "run", side_effect=self.fake_run).start()

    def fake_run(self, args, **kw):
        self.calls.append((args, kw))
        if "status" in args:
            return subprocess.CompletedProcess(args, 0, "", self.login)
        self.prompt = kw["stdin"].read().decode("utf-8") if "stdin" in kw else kw.get("input", "")
        self.inspect(args, kw)
        return subprocess.CompletedProcess(args, self.exitcode, self.output, "private diagnostic")

    def test_generated_pictures_are_collected_from_the_event_stream(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"fake image bytes"
        lines = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"style":"bold"}'}},
            {"type": "item.completed", "item": {"type": "image_generation_call", "status": "completed",
                                                "result": base64.b64encode(png).decode()}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
        result = subprocess.CompletedProcess([], 0, "\n".join(json.dumps(x) for x in lines), "")
        text, sent, wrote, images = codex._answer(result, True)
        self.assertEqual(json.loads(text), {"style": "bold"})
        self.assertEqual(images, [png])

    def test_svg_markup_is_kept_for_later_conversion(self):
        lines = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"style":"bold"}'}},
            {"type": "item.completed", "item": {"type": "image_generation_call", "status": "completed",
                                                "result": '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
        result = subprocess.CompletedProcess([], 0, "\n".join(json.dumps(x) for x in lines), "")
        _text, _sent, _wrote, images = codex._answer(result, True)
        self.assertTrue(images[0].startswith(b"<svg"))

    def test_json_answer_usage_and_stdin(self):
        prompt = "ação " + "x" * 40000 + " $(do-not-run)"
        self.assertEqual(codex.run("default", prompt), ('{"items":["ok"]}', 20, 3))
        args, kw = self.calls[-1]
        self.assertNotIn(prompt, args)
        self.assertEqual(self.prompt, prompt)
        self.assertIn("stdin", kw)
        self.assertEqual(args[-1], "-")
        self.assertNotIn("--model", args)
        self.assertFalse(kw.get("shell", False))
        self.assertEqual(kw["encoding"], "utf-8")

    def test_child_requires_subscription_without_api_overrides(self):
        codex.run("chosen-model", "hello")
        args, kw = self.calls[-1]
        self.assertEqual(args[args.index("--model") + 1], "chosen-model")
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            self.assertNotIn(name, kw["env"])
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn("--ignore-user-config", args)
        self.assertIn("--ignore-rules", args)
        self.assertIn("--ephemeral", args)
        self.assertIn("read-only", args)
        self.assertIn("features.shell_tool=false", args)
        self.assertIn('web_search="disabled"', args)
        for feature in ("apps", "plugins", "hooks", "image_generation", "view_image", "unified_exec", "multi_agent_v2",
                        "browser_use", "computer_use"):
            self.assertIn(f"features.{feature}=false", args)
        self.assertFalse(Path(kw["cwd"]).exists())

    def test_drawing_has_its_own_instructions_and_keeps_the_analysis_path_restricted(self):
        seen = {}
        def inspect(args, kw):
            option = next(a for a in args if a.startswith("model_instructions_file="))
            seen["text"] = Path(json.loads(option.split("=", 1)[1])).read_text(encoding="utf-8")
            seen["args"] = args
        self.inspect = inspect
        codex.run("default", "hello")
        self.assertIn("Do not use tools", seen["text"])
        self.assertIn("features.image_generation=false", seen["args"])
        codex.run("default", "hello", drawing=True)
        self.assertNotIn("Do not use tools", seen["text"])
        self.assertIn("picture tool", seen["text"].lower())
        self.assertNotIn("features.image_generation=false", seen["args"])

    def test_draw_never_fills_a_short_sequence_with_other_peoples_pictures(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"fake image bytes"
        lines = [
            {"type": "item.completed", "item": {"type": "agent_message", "text": '{"style":"bold"}'}},
            *[{"type": "item.completed", "item": {"type": "image_generation_call", "status": "completed",
                                                 "result": base64.b64encode(png).decode()}} for _ in range(4)],
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
        self.output = "\n".join(json.dumps(x) for x in lines)
        with self.assertRaisesRegex(codex.CodexError, "4 of the 6"):
            codex.draw("default", "draw these six poses")
        with patch.object(codex, "run", return_value=('{"style":"bold"}', 10, 2, [png] * 6)) as run:
            text, _sent, _wrote, images = codex.draw("default", "draw these six poses")
        self.assertEqual(json.loads(text), {"style": "bold"})
        self.assertEqual(len(images), 6)
        self.assertTrue(run.call_args.kwargs["drawing"])

    def test_chosen_reasoning_effort_is_sent_to_the_cli(self):
        codex.run("chosen-model", "hello", reasoning_effort="xhigh")
        self.assertIn('model_reasoning_effort="xhigh"', self.calls[-1][0])

    def test_invalid_reasoning_effort_does_not_start_codex(self):
        with self.assertRaises(ValueError):
            codex.run("chosen-model", "hello", reasoning_effort='high"; run')
        self.assertFalse(self.calls)

    def test_api_login_is_rejected_without_starting_a_turn(self):
        self.login = "Logged in using an API key: sk-private"
        with self.assertRaisesRegex(codex.CodexError, "codex login"):
            codex.run("default", "hello")
        self.assertFalse(any("exec" in args for args, _ in self.calls))
        self.assertNotIn("sk-private", json.dumps(codex.status()))

    def test_schema_is_strict_without_changing_the_original(self):
        schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "title": {"type": "STRING"}, "score": {"type": "INTEGER"}}, "required": ["title"]}}
        original = json.dumps(schema)
        def inspect(args, kw):
            parsed = json.loads(Path(args[args.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            self.assertEqual(parsed["required"], ["items"])
            self.assertFalse(parsed["additionalProperties"])
            clip = parsed["properties"]["items"]["items"]
            self.assertFalse(clip["additionalProperties"])
            self.assertEqual(clip["required"], ["title", "score"])
            self.assertEqual(clip["properties"]["score"]["anyOf"], [{"type": "integer"}, {"type": "null"}])
        self.inspect = inspect
        codex.run("default", "hello", schema=schema)
        self.assertEqual(json.dumps(schema), original)

    def test_optional_null_fields_keep_the_original_omission_semantics(self):
        schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "title": {"type": "STRING"}, "extra": {"type": "STRING"}}, "required": ["title"]}}
        self.output = events('{"items":[{"title":"ok","extra":null}]}')
        text, _, _ = codex.run("default", "hello", schema=schema)
        self.assertEqual(json.loads(text), {"items": [{"title": "ok"}]})

    def test_truncated_or_out_of_order_jsonl_is_rejected(self):
        for output in (events() + '\n{"type":"turn.failed"',
                       '\n'.join(reversed(events().split('\n')))):
            self.output = output
            with self.assertRaises(codex.CodexError):
                codex.run("default", "hello")

    def test_unicode_line_separators_inside_json_are_preserved(self):
        self.output = events("one\u2028two").replace("\\u2028", "\u2028")
        self.assertEqual(codex.run("default", "hello")[0], "one\u2028two")

    def test_image_is_attached_and_removed(self):
        self.output = events("facecam: top left")
        paths = []
        def inspect(args, kw):
            path = Path(args[args.index("--image") + 1])
            self.assertEqual(path.read_bytes(), b"jpeg-bytes")
            paths.append(path)
        self.inspect = inspect
        self.assertEqual(codex.run("default", "look", image=b"jpeg-bytes")[0], "facecam: top left")
        self.assertFalse(paths[0].exists())

    def test_missing_or_failed_terminal_event_is_not_success(self):
        for output, code in [(events().rsplit("\n", 1)[0], 0), (events(), 1),
                             (events() + '\n{"type":"turn.failed","error":{"message":"private"}}', 0)]:
            with self.subTest(output=output[-60:], code=code):
                self.output, self.exitcode = output, code
                with self.assertRaises(codex.CodexError) as caught:
                    codex.run("default", "hello")
                self.assertNotIn("private", str(caught.exception))

    def test_quota_and_auth_errors_are_actionable_and_redacted(self):
        for reason, expected in [("429 usage limit sk-private", "limit"),
                                 ("401 refresh_token_expired sk-private", "codex login"),
                                 ("model not supported sk-private", "model")]:
            with self.subTest(reason=reason):
                self.output = json.dumps({"type": "turn.failed", "error": {"message": reason}})
                with self.assertRaisesRegex(codex.CodexError, expected) as caught:
                    codex.run("default", "hello")
                self.assertNotIn("sk-private", str(caught.exception))

    def test_timeout_has_a_finite_budget_and_releases_the_lock(self):
        def timeout(args, kw):
            self.assertGreater(kw["timeout"], 0)
            self.assertLessEqual(kw["timeout"], 0.5)
            raise subprocess.TimeoutExpired(args, kw["timeout"], output="secret")
        self.inspect = timeout
        with self.assertRaisesRegex(codex.CodexError, "timed out"):
            codex.run("default", "hello", timeout=0.5)
        self.assertLessEqual(self.command.call_args.kwargs.get("timeout", 999), 0.5)
        self.inspect = lambda args, kw: None
        self.assertEqual(codex.run("default", "hello")[1:], (20, 3))

    def test_missing_executable_explains_installation(self):
        self.command.return_value = []
        state = codex.status()
        self.assertFalse(state["installed"])
        with self.assertRaisesRegex(codex.CodexError, "install|Install|found"):
            codex.run("default", "hello")

    def test_queue_wait_respects_the_call_deadline(self):
        codex._lock.acquire()
        try:
            with self.assertRaisesRegex(codex.CodexError, "timed out"):
                codex.run("default", "hello", timeout=0.01)
            self.assertFalse(self.calls)
        finally:
            codex._lock.release()

    def test_model_list_uses_the_cli_cache_without_api_requests(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"CODEX_HOME": directory}):
            Path(directory, "models_cache.json").write_text(json.dumps({"models": [
                {"slug": "chosen-model", "visibility": "list"},
                {"slug": "hidden-model", "visibility": "hide"}]}), encoding="utf-8")
            self.assertEqual(codex.list_models(), ["default", "chosen-model"])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        from studio import ai, config
        self.ai = ai
        self.assertIn("codex", ai.PROVIDERS, "PeakCut must offer the Codex subscription provider")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patch.object(config, "DATA_DIR", Path(self.directory.name)).start()
        patch.object(config, "AI_PROVIDER", "codex").start()
        patch.object(config, "AI_MODELS", ["default"]).start()
        patch.object(config, "AI_BASE_URL", "https://stale.example").start()
        patch.dict(ai._saved, {"at": None, "data": {}}).start()
        patch.object(codex, "command", return_value=["codex.exe"]).start()
        patch.object(codex, "status", return_value={"installed": True, "subscription": True, "error": ""}).start()
        self.addCleanup(patch.stopall)

    def test_save_needs_no_key_and_drops_stale_address(self):
        saved = self.ai.save("codex", "default", "https://stale.example", "api-secret")
        self.assertTrue(saved["ready"])
        self.assertFalse(saved["has_key"])
        self.assertEqual(self.ai.key(), "")
        self.assertEqual(self.ai.base_url(), "")
        row = next(p for p in saved["providers"] if p["id"] == "codex")
        self.assertFalse(row["needs_key"])
        self.assertNotIn("api-secret", (Path(self.directory.name) / "ai.json").read_text())

    def test_text_calls_keep_the_existing_contract(self):
        with patch.object(codex, "run", return_value=('{"items":["ok"]}', 20, 3)) as run:
            schema = {"type": "ARRAY", "items": {"type": "STRING"}}
            self.assertEqual(self.ai.call("default", "hello", schema, 12), (["ok"], 20, 3))
            self.assertEqual(run.call_args.kwargs.get("timeout"), 12)

    def test_vision_uses_the_same_subscription(self):
        with patch.object(codex, "run", return_value=("top left", 11, 2)) as run:
            self.assertEqual(self.ai.see("look", b"jpeg", 8), ("top left", 11, 2))
            self.assertEqual(run.call_args.kwargs["image"], b"jpeg")
            self.assertEqual(run.call_args.kwargs["timeout"], 8)

    def test_model_list_and_errors_use_the_adapter(self):
        with patch.object(codex, "list_models", return_value=["default", "chosen-model"]):
            self.assertEqual(self.ai.list_models("codex"), ["default", "chosen-model"])
        reason = "Codex subscription usage limit reached. Try again later."
        self.assertEqual(self.ai.explain(codex.CodexError(reason)), reason)

    def test_effort_setting_is_kept_for_text_and_image_requests(self):
        self.ai.save("codex", "chosen-model", reasoning_effort="high")
        self.assertEqual(self.ai.current()["reasoning_effort"], "high")
        with patch.object(codex, "run", return_value=('{"items":["ok"]}', 20, 3)) as run:
            self.ai.call("chosen-model", "hello", {"type": "ARRAY", "items": {"type": "STRING"}})
            self.assertEqual(run.call_args.kwargs["reasoning_effort"], "high")
            self.ai.see("look", b"jpeg")
            self.assertEqual(run.call_args.kwargs["reasoning_effort"], "high")

    def test_an_api_login_or_logged_out_cli_is_not_ready(self):
        with patch.object(codex, "status", return_value={"installed": True, "subscription": False,
                                                       "error": codex.LOGIN}):
            self.assertFalse(self.ai.ready())
            self.assertFalse(self.ai.public()["ready"])


class ProcessTests(unittest.TestCase):
    def test_long_stdin_respects_timeout_even_when_child_does_not_read(self):
        start = time.monotonic()
        with self.assertRaisesRegex(codex.CodexError, "timed out"):
            codex._process([sys.executable, "-c", "import time; time.sleep(1)"], 0.1, input="ação" * 300000)
        self.assertLess(time.monotonic() - start, 0.7)

    def test_npm_installation_runs_the_native_binary_without_node_children(self):
        for layout in ("optional", "legacy"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shim = root / "codex.cmd"
                shim.write_text("@echo off", encoding="utf-8")
                package = root / "node_modules" / "@openai" / ("codex-win32-x64" if layout == "optional" else "codex")
                binary = package / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
                binary.parent.mkdir(parents=True)
                binary.touch()
                with patch.object(codex.shutil, "which", return_value=str(shim)), patch.object(sys, "platform", "win32"), \
                     patch("platform.machine", return_value="AMD64"), patch.dict(os.environ, {"PEAKCUT_CODEX_COMMAND": ""}):
                    self.assertEqual(codex.command(), [str(binary)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
