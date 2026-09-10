"""Offline container checks, passed to the built image through Python stdin.

Run with a read-only container root and a writable /tmp, without a database
mount or provider key. The probe checks Linux wheels and the empty-data path.
"""
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import ssl
import subprocess
import tempfile

import cffi
import cryptography
import duckdb
import numpy as np
import pandas as pd
import pdfplumber
from PIL import Image
import pyarrow
import pydantic_core
import pypdfium2
import rpds
import scipy.linalg
import statsmodels.api
from fastapi.testclient import TestClient

from app.server import create_app


def main():
    assert os.getuid() == os.getgid() == 10001
    subprocess.run(["python", "-m", "pip", "check"], check=True, capture_output=True)
    assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0
    for path in ("/app/.git", "/app/.env", "/app/.env.example", "/app/tmp",
                 "/app/tools", "/app/data_pipeline/lakehouse/analytics.duckdb",
                 "/app/app/.env.codex-docker-ignore-probe"):
        assert not Path(path).exists(), path

    data = pd.DataFrame({"value": pd.array([2**53 + 1, None], dtype="Int64")})
    buffer = BytesIO()
    data.to_parquet(buffer)
    buffer.seek(0)
    restored = pd.read_parquet(buffer)
    assert int(restored.iloc[0, 0]) == 2**53 + 1 and pd.isna(restored.iloc[1, 0])
    assert np.allclose(scipy.linalg.solve([[2., 0.], [0., 4.]], [2., 8.]), [1., 2.])
    with duckdb.connect() as db:
        assert db.execute("SELECT 9007199254740993::BIGINT").fetchone()[0] == 2**53 + 1
    pdf = BytesIO()
    Image.new("RGB", (80, 60), "white").save(pdf, format="PDF")
    document = pypdfium2.PdfDocument(pdf.getvalue())
    page = document[0]
    bitmap = page.render(scale=1)
    assert bitmap.width > 0 and bitmap.height > 0
    bitmap.close()
    page.close()
    document.close()
    with pdfplumber.open(BytesIO(pdf.getvalue())) as parsed:
        assert len(parsed.pages) == 1

    with tempfile.TemporaryDirectory() as directory:
        app = create_app(runtime_root=Path(directory))
        try:
            with TestClient(app) as client:
                status = client.get("/api/status").json()
                assert status["status"] == "ok"
                assert not status["finance_available"] and not status["provider_ready"]
                assert client.post("/api/workspaces", json={"profile": "finance"}).status_code == 409
                workspace = client.post("/api/workspaces", json={"profile": "generic"})
                assert workspace.status_code == 200
                identifier = workspace.json()["workspace_id"]
                assert client.post(f"/api/workspaces/{identifier}/runs",
                                   json={"message": "Offline probe"}).status_code == 503
                assert client.get("/static/vendor/echarts.min.js").status_code == 200
        finally:
            app.state.context.pool.shutdown(wait=True)
    print(json.dumps({"status": "passed", "platform": platform.machine(),
                      "python": platform.python_version(), "uid": os.getuid(),
                      "pip_check": True, "binary_imports": True, "pdf_raster": True,
                      "parquet_int64": True, "empty_database_path": True,
                      "build_exclusions": True, "model_calls": 0}))


if __name__ == "__main__":
    main()
