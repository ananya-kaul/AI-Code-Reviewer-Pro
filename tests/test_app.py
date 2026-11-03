"""Checks for source file import and workspace state actions."""

import tempfile
import unittest
from pathlib import Path

import app
from utils import detect_language, language_from_filename


class WorkspaceTests(unittest.TestCase):
    def test_file_extension_selects_language(self):
        self.assertEqual(language_from_filename("component.TSX"), "TypeScript")
        self.assertEqual(language_from_filename("styles.css"), "HTML/CSS")
        self.assertIsNone(language_from_filename("notes.txt"))

    def test_typed_function_is_detected_as_typescript(self):
        language, confidence = detect_language(
            "function total(values: number[]): number { return values.reduce((a, b) => a + b, 0); }"
        )
        self.assertEqual(language, "TypeScript")
        self.assertGreaterEqual(confidence, .25)

    def test_import_code_file(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "example.ts"
            path.write_text("const answer: number = 42;", encoding="utf-8")
            code, language, autodetect, message, *results = app.load_code_file(str(path))

        self.assertEqual(code["value"], "const answer: number = 42;")
        self.assertEqual(language["value"], "TypeScript")
        self.assertFalse(autodetect["value"])
        self.assertIn("example.ts", message["value"])
        self.assertEqual(results[0], app.SCORE_PLACEHOLDER)

    def test_import_rejects_oversized_file_without_changing_editor(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "large.py"
            path.write_text("x" * (app.MAX_CODE_CHARS + 1), encoding="utf-8")
            code, language, autodetect, message, *results = app.load_code_file(str(path))

        self.assertNotIn("value", code)
        self.assertNotIn("value", language)
        self.assertNotIn("value", autodetect)
        self.assertIn("too large", message["value"])
        self.assertNotIn("value", results[0])

    def test_editor_stats_warns_before_review(self):
        self.assertIn("30,001 / 30,000", app.editor_stats("x" * 30_001))
        self.assertIn("Trim the snippet", app.editor_stats("x" * 30_001))

    def test_clear_history_disables_restore(self):
        history, view, picker, reopen = app.clear_history()
        self.assertEqual(history, [])
        self.assertIn("No reviews yet", view)
        self.assertEqual(picker["choices"], [])
        self.assertFalse(reopen["interactive"])


if __name__ == "__main__":
    unittest.main()
