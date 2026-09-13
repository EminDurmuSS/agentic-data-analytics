"""Functional Chromium coverage, optional when Playwright is not installed.

Run with PLAYWRIGHT_MODULE pointing to an installed Playwright package, or with
playwright available through Node's normal module lookup. No model calls occur.
"""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


class ProductPresentationTests(unittest.TestCase):
    def test_real_dom_display_proof_lazy_details_and_safe_markdown(self):
        self._browser_script("product_presentation.cjs")

    def test_empty_workspace_source_selection_and_interrupted_analysis(self):
        self._browser_script("source_workflow.cjs")

    def _browser_script(self, script):
        if not shutil.which("node"):
            self.skipTest("Node is required for the Chromium product test")
        root = Path(__file__).parents[2]
        available = subprocess.run(
            ["node", "-e", "require(process.env.PLAYWRIGHT_MODULE || 'playwright')"],
            cwd=root, capture_output=True, env=os.environ, timeout=15,
        )
        if available.returncode:
            self.skipTest("Install Playwright or set PLAYWRIGHT_MODULE for the functional browser test")
        result = subprocess.run(
            ["node", "tests/app/" + script], cwd=root,
            capture_output=True, text=True, env=os.environ, timeout=75,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("functional browser assertions passed", result.stdout)
