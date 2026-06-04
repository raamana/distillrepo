from __future__ import annotations

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from distillrepo.analysis import clean_source
from distillrepo.models import Config


class SourceCleaningTests(unittest.TestCase):
    def test_clean_source_strips_trailing_whitespace_and_collapses_large_blank_runs(self) -> None:
        source = "\n".join(
            [
                "# ===== FILE: generated.py",
                "",
                "",
                "def first():    ",
                "    return 1\t",
                "",
                "",
                "",
                "",
                "def second():",
                "    return 2",
                "",
                "",
                "",
            ]
        )

        header_pattern = Config.__dataclass_fields__["header_strip_pattern"].default
        cleaned = clean_source(source, header_pattern)

        self.assertEqual(
            cleaned,
            "\n".join(
                [
                    "def first():",
                    "    return 1",
                    "",
                    "",
                    "def second():",
                    "    return 2",
                    "",
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
