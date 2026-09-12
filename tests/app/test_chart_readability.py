"""Actual ECharts geometry, disclosure and export checks through Chromium.

Requires Node and Playwright. PLAYWRIGHT_MODULE may point to an external package.
All API data is synthetic and no live model or external source requests occur.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


class ChartReadabilityTests(unittest.TestCase):
    def test_real_chart_scales_warnings_single_period_and_export(self):
        if not shutil.which("node"):
            self.skipTest("Node is required for the Chromium chart test")
        root = Path(__file__).parents[2]
        available = subprocess.run(
            ["node", "-e", "require(process.env.PLAYWRIGHT_MODULE || 'playwright')"],
            cwd=root, capture_output=True, env=os.environ, timeout=15,
        )
        if available.returncode:
            self.skipTest("Install Playwright or set PLAYWRIGHT_MODULE for the functional browser test")
        result = subprocess.run(
            ["node", "tests/app/chart_readability.cjs"], cwd=root,
            capture_output=True, text=True, env=os.environ, timeout=90,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("functional Chromium assertions passed", result.stdout)
