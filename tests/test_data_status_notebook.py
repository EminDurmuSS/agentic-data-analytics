import contextlib
import io
import unittest
from pathlib import Path

import nbformat


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = (
    ROOT / "notebooks" / "KKB_Verileri_Dogrulanmis.ipynb",
    ROOT / "data_pipeline" / "KKB_Verileri_Dogrulanmis.ipynb",
)


class DataStatusNotebookTests(unittest.TestCase):
    def test_notebooks_describe_current_data_scope(self) -> None:
        for path in NOTEBOOKS:
            notebook = nbformat.read(path, as_version=4)
            content = "\n".join(
                "".join(cell.get("source", "")) for cell in notebook.cells
            )
            self.assertIn("BDDK haftalık", content, path)
            self.assertIn("BDDK FinTürk", content, path)
            self.assertIn("52.696", content, path)
            self.assertIn("İl bazlı konut paneli", content, path)
            self.assertIn("506", content, path)
            self.assertIn("analytics.duckdb", content, path)
            self.assertNotIn("25 sayısal seri", content, path)
            self.assertNotIn("BDDK aylık/haftalık/FinTürk kapsamı tamamlanmadı", content, path)

    def test_notebook_copies_are_identical(self) -> None:
        self.assertEqual(NOTEBOOKS[0].read_bytes(), NOTEBOOKS[1].read_bytes())

    def test_notebook_code_runs_against_current_duckdb(self) -> None:
        notebook = nbformat.read(NOTEBOOKS[0], as_version=4)
        namespace = {"__name__": "__notebook_test__"}
        with contextlib.redirect_stdout(io.StringIO()):
            for cell in notebook.cells:
                if cell.cell_type == "code":
                    exec(compile(cell.source, str(NOTEBOOKS[0]), "exec"), namespace)


if __name__ == "__main__":
    unittest.main()
