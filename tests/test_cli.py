from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


class CliTests(unittest.TestCase):
    def test_no_args_writes_full_bundle_to_current_directory_without_ir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_sample_project(project)

            result = self._run_cli([], cwd=project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("IR: skipped (--no-ir)", result.stdout)
            bundles = sorted(project.glob("distilled.sample_pkg.*.py"))
            self.assertEqual(len(bundles), 1)
            self.assertFalse((project / "src" / "sample_pkg" / ".distillrepo").exists())
            self.assertIn("# Review mode: full", bundles[0].read_text(encoding="utf-8"))

    def test_missing_input_path_reports_cli_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            result = self._run_cli([str(missing)], cwd=Path(tmp))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("distillrepo: error: Path does not exist", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_output_parent_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_sample_project(project)
            output = project / "artifacts" / "review" / "bundle.py"

            result = self._run_cli([str(project), "--output", str(output), "--no-ir"], cwd=project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())

    def test_project_root_entry_point_outside_package_is_included(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_sample_project(project)
            scripts = project / "scripts"
            scripts.mkdir()
            (scripts / "run_dashboard.py").write_text(
                "\n".join(
                    [
                        "from sample_pkg.worker import work",
                        "",
                        "",
                        "def main() -> None:",
                        "    work()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            output = project / "bundle.py"

            result = self._run_cli(
                [
                    str(project),
                    "--entry-point-module",
                    "scripts/run_dashboard.py",
                    "--entry-point-function",
                    "main",
                    "--review-mode",
                    "full",
                    "--no-ir",
                    "--output",
                    str(output),
                ],
                cwd=project,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            bundle = output.read_text(encoding="utf-8")
            self.assertIn("# Entry point: scripts/run_dashboard.py:main", bundle)
            self.assertIn("# FILE: scripts/run_dashboard.py", bundle)
            self.assertIn("# FILE: src/sample_pkg/worker.py", bundle)

    def test_version_reports_source_version(self) -> None:
        result = self._run_cli(["--version"], cwd=REPO_ROOT)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "distillrepo 0.3")

    def _run_cli(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "distillrepo", *args],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_sample_project(self, project: Path) -> None:
        package = project / "src" / "sample_pkg"
        package.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            "\n".join(
                [
                    "[project]",
                    'name = "sample-pkg"',
                    'version = "0.1.0"',
                    "",
                    "[project.scripts]",
                    'sample-pkg = "sample_pkg.cli:main"',
                    "",
                    "[tool.hatch.build.targets.wheel]",
                    'packages = ["src/sample_pkg"]',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (package / "__init__.py").write_text('"""Sample package."""\n', encoding="utf-8")
        (package / "cli.py").write_text(
            "\n".join(
                [
                    "from .worker import work",
                    "",
                    "",
                    "def main() -> None:",
                    "    work()",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (package / "worker.py").write_text(
            "\n".join(
                [
                    "def work() -> str:",
                    '    return "done"',
                    "",
                ]
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
