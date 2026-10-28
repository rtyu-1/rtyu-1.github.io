import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("citation_refresh", Path(__file__).resolve().parents[1] / "main.py")
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


class CitationRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.previous_cwd = Path.cwd()
        os.chdir(self.directory)
        self.addCleanup(os.chdir, self.previous_cwd)
        Path("results").mkdir()
        self.old_data = '{"updated":"2026-10-20","citedby":14}'
        Path("results/gs_data.json").write_text(self.old_data)
        Path("results/gs_data_shieldsio.json").write_text('{"message":"14"}')
        environment = patch.dict(os.environ, {
            "GITHUB_OUTPUT": str(self.directory / "output"),
            "GITHUB_STEP_SUMMARY": str(self.directory / "summary"),
        })
        environment.start()
        self.addCleanup(environment.stop)

    def assert_snapshot_preserved(self):
        self.assertEqual(Path("results/gs_data.json").read_text(), self.old_data)
        self.assertEqual(Path("results/gs_data_shieldsio.json").read_text(), '{"message":"14"}')
        self.assertEqual(Path("output").read_text().strip(), "refreshed=false")

    def test_unavailable_preserves_snapshot_and_date(self):
        with patch.object(refresh.subprocess, "run", return_value=subprocess.CompletedProcess([], 75, "", "Missing profile")):
            refresh.main()
        self.assert_snapshot_preserved()

    def test_timeout_preserves_snapshot_and_date(self):
        with patch.object(refresh.subprocess, "run", side_effect=subprocess.TimeoutExpired("fetch", 120)):
            refresh.main()
        self.assert_snapshot_preserved()

    def test_success_writes_valid_snapshots(self):
        author = {"name": "Test Author", "citedby": 18, "hindex": 3,
                  "publications": [{"author_pub_id": "test:paper", "num_citations": 2}]}
        with patch.object(refresh.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(author), "")):
            refresh.main()
        actual = json.loads(Path("results/gs_data.json").read_text())
        self.assertEqual(actual["citedby"], 18)
        self.assertIn("test:paper", actual["publications"])
        self.assertNotEqual(actual["updated"], "2026-10-20")
        self.assertEqual(json.loads(Path("results/gs_data_shieldsio.json").read_text())["message"], "18")
        self.assertEqual(Path("output").read_text().strip(), "refreshed=true")

    def test_unexpected_error_is_not_suppressed(self):
        with patch.object(refresh.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Unexpected error")), self.assertRaises(RuntimeError):
            refresh.main()
        self.assertEqual(Path("results/gs_data.json").read_text(), self.old_data)
        self.assertFalse(Path("output").exists())

    def test_invalid_data_does_not_replace_snapshot(self):
        author = {"name": "Test", "citedby": -1, "hindex": 0, "publications": []}
        with patch.object(refresh.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(author), "")), self.assertRaises(ValueError):
            refresh.main()
        self.assertEqual(Path("results/gs_data.json").read_text(), self.old_data)

    def test_only_upstream_missing_element_error_is_classified(self):
        source = "def _fill_basics():\n    return None.text\n"
        namespace = {}
        exec(compile(source, "/site-packages/scholarly/author_parser.py", "exec"), namespace)
        try:
            namespace["_fill_basics"]()
        except AttributeError as error:
            self.assertTrue(refresh.is_profile_parse_error(error))
        try:
            None.text
        except AttributeError as error:
            self.assertFalse(refresh.is_profile_parse_error(error))


if __name__ == "__main__":
    unittest.main()
