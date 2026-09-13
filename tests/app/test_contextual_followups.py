"""Run the real contextual-followup UI against controlled synthetic HTTP data."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


class ContextualFollowupsBrowserTests(unittest.TestCase):
    def test_run_scoped_questions_races_prefill_and_mobile(self):
        if not shutil.which("node"):
            self.skipTest("Node is required for the Chromium follow-up test")
        root = Path(__file__).parents[2]
        available = subprocess.run(
            ["node", "-e", "require(process.env.PLAYWRIGHT_MODULE || 'playwright')"],
            cwd=root, capture_output=True, env=os.environ, timeout=15,
        )
        if available.returncode:
            self.skipTest("Install Playwright or set PLAYWRIGHT_MODULE")
        result = subprocess.run(
            ["node", "tests/app/contextual_followups.cjs"], cwd=root,
            capture_output=True, text=True, env=os.environ, timeout=90,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("contextual followups functional Chromium assertions passed", result.stdout)
