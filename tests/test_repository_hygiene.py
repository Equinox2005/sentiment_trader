import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def test_importing_app_factory_does_not_open_production_storage(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "must-not-be-created.sqlite3"
            environment = os.environ.copy()
            environment["PLAYBOOK_DATA_CACHE"] = str(database)
            environment["PLAYBOOK_ENABLE_SCHEDULER"] = "0"

            result = subprocess.run(
                [sys.executable, "-c", "import app"],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                database.exists(),
                "Importing the application factory opened persistent storage.",
            )
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        render_blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("wsgi:app", dockerfile)
        self.assertIn("wsgi:app", render_blueprint)
        self.assertNotIn("app:app", dockerfile)
        self.assertNotIn("app:app", render_blueprint)

    def test_docker_context_excludes_runtime_data_and_secrets(self):
        patterns = {
            line.strip()
            for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {"instance/", "*.sqlite3*", ".env", "*.log"}.issubset(patterns)
        )

    def test_ci_runs_the_complete_unittest_suite_on_push_and_pull_request(self):
        workflow_path = ROOT / ".github" / "workflows" / "tests.yml"

        self.assertTrue(workflow_path.exists())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("push:", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)

    def test_public_copy_names_the_default_universe_instead_of_the_whole_market(self):
        readme_intro = "\n".join(
            (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:8]
        )
        board = (ROOT / "templates" / "board.html").read_text(encoding="utf-8")
        scanner = (ROOT / "scanner.py").read_text(encoding="utf-8")
        accurate_scope = "Nasdaq-listed common stocks plus current S&P 500 constituents"

        self.assertNotIn("whole market", readme_intro)
        self.assertNotIn("every US-listed common stock", readme_intro)
        self.assertNotIn("Every US-listed stock", board)
        self.assertNotIn("Every listed stock", board)
        self.assertNotIn("Every listed stock", scanner)
        self.assertIn(accurate_scope, readme_intro)
        self.assertIn(accurate_scope, board)
        self.assertIn(accurate_scope, scanner)

    def test_dependencies_are_exactly_pinned_and_hash_locked(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        dependency_lines = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        lock_path = ROOT / "requirements.lock"

        self.assertTrue(all("==" in line for line in dependency_lines))
        self.assertNotIn(">=", requirements)
        self.assertNotIn("<", requirements)
        self.assertTrue(lock_path.exists())
        lock = lock_path.read_text(encoding="utf-8")
        self.assertIn("--hash=sha256:", lock)

        workflow = (
            ROOT / ".github" / "workflows" / "tests.yml"
        ).read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        render_blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        locked_install = "--require-hashes -r requirements.lock"
        self.assertIn(locked_install, workflow)
        self.assertIn(locked_install, dockerfile)
        self.assertEqual(render_blueprint.count(locked_install), 2)
        self.assertIn(locked_install, readme)
        self.assertIn("mkdir -p /app/instance", dockerfile)
        self.assertIn("chown appuser:appuser /app/instance", dockerfile)


if __name__ == "__main__":
    unittest.main()
