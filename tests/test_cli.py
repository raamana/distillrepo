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

    def test_git_mode_bundles_tracked_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_sample_project(project)
            (project / "README.md").write_text("# Sample\n", encoding="utf-8")
            (project / "NOTES.md").write_text("# Scratch\n", encoding="utf-8")
            (project / "untracked.txt").write_text("do not include\n", encoding="utf-8")
            output = project / "git-bundle.txt"
            subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
            subprocess.run(
                ["git", "add", "README.md", "NOTES.md", "pyproject.toml", "src/sample_pkg/__init__.py", "src/sample_pkg/cli.py"],
                cwd=project,
                check=True,
                capture_output=True,
            )

            result = self._run_cli([".", "--git", "--output", str(output)], cwd=project)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Source selection: git tracked files", result.stdout)
            self.assertIn("Analyzed: 2 files", result.stdout)
            self.assertIn("Supplemental files: 3 bundled", result.stdout)
            bundle = output.read_text(encoding="utf-8")
            self.assertIn("# Distillrepo Review Bundle", bundle)
            self.assertIn("# Source selection: git tracked files", bundle)
            self.assertIn("# Files analyzed: 2", bundle)
            self.assertIn("# Supplemental files bundled: 3", bundle)
            self.assertIn("# FILE: src/sample_pkg/cli.py", bundle)
            self.assertIn("# FILE: README.md", bundle)
            self.assertIn("# FILE: NOTES.md", bundle)
            self.assertIn("# FILE: pyproject.toml", bundle)
            self.assertNotIn("untracked.txt", bundle)
            self.assertNotIn("worker.py", bundle)

    def test_include_path_bundles_multiple_selected_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            first = project / "first"
            second = project / "second"
            cache = first / "__pycache__"
            first.mkdir()
            second.mkdir()
            cache.mkdir()
            (first / "a.py").write_text("A = 1\n", encoding="utf-8")
            (first / "scratch.md").write_text("# Scratch\n", encoding="utf-8")
            (second / "b.yaml").write_text("b: 2\n", encoding="utf-8")
            (cache / "ignored.pyc").write_bytes(b"compiled")
            output = project / "paths-bundle.txt"

            result = self._run_cli(
                [
                    ".",
                    "--include-path",
                    "first",
                    "second",
                    "--output",
                    str(output),
                ],
                cwd=project,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Source selection: selected paths", result.stdout)
            self.assertIn("Analyzed: 1 files", result.stdout)
            bundle = output.read_text(encoding="utf-8")
            self.assertIn("# Distillrepo Review Bundle", bundle)
            self.assertIn("# Source selection: selected paths", bundle)
            self.assertIn("# FILE: first/a.py", bundle)
            self.assertIn("# FILE: second/b.yaml", bundle)
            self.assertIn("scratch.md: non-code file not declared in pyproject", bundle)
            self.assertNotIn("# FILE: first/scratch.md", bundle)
            self.assertIn("ignored.pyc: excluded directory", bundle)
            self.assertNotIn("compiled", bundle)

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

    def test_explicit_entry_module_can_be_module_level_without_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_sample_project(project)
            scripts = project / "src" / "sample_pkg" / "scripts"
            scripts.mkdir()
            (scripts / "run-dashboard.py").write_text(
                "\n".join(
                    [
                        "from sample_pkg.worker import work",
                        "",
                        "DASHBOARD_RESULT = work()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            output = project / "bundle.py"

            result = self._run_cli(
                [
                    str(project / "src" / "sample_pkg"),
                    "--entry-point-module",
                    "scripts/run-dashboard.py",
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
            self.assertIn("# Entry point: scripts/run-dashboard.py", bundle)
            self.assertIn("# Call Graph\n- n/a (module-level entrypoint; no single function selected)", bundle)
            self.assertIn("# FILE: scripts/run-dashboard.py", bundle)

    def test_version_reports_source_version(self) -> None:
        result = self._run_cli(["--version"], cwd=REPO_ROOT)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "distillrepo 0.5.0")

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
                    'readme = "README.md"',
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
