"""Actual browser coverage for readable activity, using synthetic HTTP fixtures.

Requires Node and Playwright. Set PLAYWRIGHT_MODULE when Playwright is installed
outside the repository. The test makes no provider calls and no workspace writes.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


class ActivityJourneyTests(unittest.TestCase):
    def test_real_chromium_journey_polling_disclosures_attention_and_mobile(self):
        if not shutil.which("node"):
            self.skipTest("Node is required for the Chromium activity test")
        root = Path(__file__).parents[2]
        available = subprocess.run(
            ["node", "-e", "require(process.env.PLAYWRIGHT_MODULE || 'playwright')"],
            cwd=root, capture_output=True, env=os.environ, timeout=15,
        )
        if available.returncode:
            self.skipTest("Install Playwright or set PLAYWRIGHT_MODULE for the functional browser test")
        result = subprocess.run(
            ["node", "tests/app/activity_journey.cjs"], cwd=root,
            capture_output=True, text=True, env=os.environ, timeout=75,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("functional Chromium assertions passed", result.stdout)
