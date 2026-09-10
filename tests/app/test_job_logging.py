"""Failed jobs remain diagnosable without exporting provider or document secrets."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agentic_analytics.lakehouse.service import PlanError
from agentic_analytics.lakehouse.store import StoreError
from agentic_analytics.providers.mia import MiaError
from app.diagnostics import log_job_failure
from app.server import create_app


class OpaqueFailure(RuntimeError):
    def __str__(self):
        raise AssertionError("Exception text must not be inspected")

    def __repr__(self):
        raise AssertionError("Exception repr must not be inspected")


class JobLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(runtime_root=Path(self.temp.name), source_db=None,
                              client=object(), searxng_url=False)
        self.context = self.app.state.context
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.workspace = self.client.post("/api/workspaces", json={"profile": "generic"}).json()["workspace_id"]

    def tearDown(self):
        self.context.pool.shutdown(wait=True)
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def fail_job(self, error, *, cause=None):
        class FailingRuntime:
            def run(self, *args, **kwargs):
                if cause is not None:
                    try:
                        raise cause
                    except Exception as caught:
                        raise error from caught
                raise error

        with patch.object(self.context, "runtime", return_value=FailingRuntime()):
            response = self.client.post(f"/api/workspaces/{self.workspace}/runs", json={"message": "Exercise worker failure"})
            self.assertEqual(response.status_code, 200)
            job_id = response.json()["job_id"]
            self.context.futures[job_id].result(timeout=10)
        response = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assert_safe_records(self, captured, secrets):
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertFalse(record.exc_info)
        self.assertIsNone(record.stack_info)
        self.assertTrue(all(isinstance(arg, str) for arg in record.args))
        logged = record.getMessage()
        self.assertNotIn("\n", logged)
        for secret in secrets:
            self.assertNotIn(secret, logged)
            self.assertNotIn(secret, str(record.__dict__))
        return logged

    def test_unexpected_failure_logs_locations_without_secret_text_or_chained_cause(self):
        secrets = ["KEY_SENTINEL_98271", "DOCUMENT_SENTINEL_76419", "CAUSE_SENTINEL_19463"]
        error = RuntimeError("provider=" + secrets[0] + " document=" + secrets[1])
        with self.assertLogs("app.diagnostics", level="ERROR") as captured:
            job = self.fail_job(error, cause=ValueError(secrets[2]))
        logged = self.assert_safe_records(captured, secrets)
        self.assertIn(job["job_id"], logged)
        self.assertIn(self.workspace, logged)
        self.assertIn("error_type=RuntimeError", logged)
        self.assertIn("error_code=UNEXPECTED_ERROR", logged)
        self.assertIn("test_job_logging.py:", logged)
        self.assertIn("context.py:", logged)
        self.assertNotIn("raise error", logged)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["result"], {"status": "failed", "message": "Çalışma tamamlanamadı. Kaydedilmiş araç adımlarından yeniden deneyebilirsiniz.", "error_type": "RuntimeError"})
        self.assertEqual(job["events"], [])

    def test_typed_worker_failures_keep_existing_api_error_contract(self):
        for error, code in [(PlanError("Safe validation detail", code="UNIT_MISMATCH"), "UNIT_MISMATCH"),
                            (StoreError("Safe store detail"), "STORE_CONTRACT_ERROR")]:
            with self.subTest(code=code), self.assertLogs("app.diagnostics", level="ERROR") as captured:
                job = self.fail_job(error, cause=RuntimeError("PRIVATE_CHAIN_TEXT"))
            logged = self.assert_safe_records(captured, ["PRIVATE_CHAIN_TEXT", str(error)])
            self.assertIn("error_code=" + code, logged)
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["result"]["status"], "blocked")
            self.assertEqual(job["result"]["errors"], [{"code": code, "message": str(error)}])

    def test_logging_never_calls_exception_string_or_repr(self):
        with self.assertLogs("app.diagnostics", level="ERROR") as captured:
            job = self.fail_job(OpaqueFailure())
        logged = self.assert_safe_records(captured, [])
        self.assertIn("error_type=OpaqueFailure", logged)
        self.assertEqual(job["result"]["error_type"], "OpaqueFailure")

    def test_error_codes_are_allowlisted_and_cannot_inject_log_lines(self):
        for code in ("UNIT_MISMATCH\nPRIVATE_CODE_TEXT", "UPPERCASE_SECRET_VALUE", "X" * 300, {"private": "PRIVATE_CODE_TEXT"}):
            with self.subTest(code_type=type(code).__name__), self.assertLogs("app.diagnostics", level="ERROR") as captured:
                log_job_failure(PlanError("PRIVATE_EXCEPTION_TEXT", code=code),
                                job_id="job_" + "1" * 32, workspace_id=self.workspace)
            logged = self.assert_safe_records(captured, ["PRIVATE_CODE_TEXT", "UPPERCASE_SECRET_VALUE", "PRIVATE_EXCEPTION_TEXT"])
            self.assertIn("error_code=PLAN_ERROR", logged)
        error = RuntimeError("PRIVATE_EXCEPTION_TEXT")
        error.code = "PROVIDER_AUTH_ERROR"
        with self.assertLogs("app.diagnostics", level="ERROR") as captured:
            log_job_failure(error, job_id="job_" + "2" * 32, workspace_id=self.workspace)
        self.assertIn("error_code=UNEXPECTED_ERROR", captured.records[0].getMessage())

    def test_provider_code_is_logged_without_provider_exception_details(self):
        error = MiaError("PROVIDER_AUTH_ERROR", "BEARER_SECRET DOCUMENT_SECRET", status_code=401)
        with self.assertLogs("app.diagnostics", level="ERROR") as captured:
            job = self.fail_job(error)
        logged = self.assert_safe_records(captured, ["BEARER_SECRET", "DOCUMENT_SECRET"])
        self.assertIn("error_code=PROVIDER_AUTH_ERROR", logged)
        self.assertEqual(job["result"]["status"], "failed")
        self.assertNotIn("BEARER_SECRET", job["result"]["message"])

    def test_log_sink_failure_does_not_prevent_failed_job_persistence(self):
        with patch("app.diagnostics.LOGGER.error", side_effect=OSError("Unavailable log sink")):
            job = self.fail_job(RuntimeError("PRIVATE_EXCEPTION_TEXT"))
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["result"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
