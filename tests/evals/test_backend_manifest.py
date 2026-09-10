"""Live evaluation evidence must cover the complete backend after module splits."""
from evals.consistency import backend_manifest


def test_extracted_backend_changes_invalidate_evaluation_fingerprint(tmp_path):
    policy = tmp_path / "agentic_analytics" / "agent" / "prompts.py"
    policy.parent.mkdir(parents=True)
    policy.write_text('SYSTEM_PROMPT = "first policy"\n')
    routes = tmp_path / "app" / "routes"
    routes.mkdir(parents=True)
    initial = backend_manifest(tmp_path)

    policy.write_text('SYSTEM_PROMPT = "changed policy"\n')
    changed = backend_manifest(tmp_path)
    assert changed != initial

    route = routes / "analyses.py"
    route.write_text('"""New analysis routes."""\n')
    added = backend_manifest(tmp_path)
    assert added != changed
    assert "app/routes/analyses.py" in added

    policy.unlink()
    assert backend_manifest(tmp_path) != added
