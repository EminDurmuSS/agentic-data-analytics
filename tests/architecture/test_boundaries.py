"""Keep serving independent of the UI, ingestion scripts and evaluation harness."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "agentic_analytics"


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = ".".join(path.relative_to(ROOT).parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent = package.split(".")[:len(package.split(".")) - node.level + 1]
                module = ".".join([*parent, node.module or ""]).rstrip(".")
            else:
                module = node.module or ""
            yield module
            yield from (module + "." + item.name for item in node.names)


@pytest.mark.parametrize("layer", ["agent", "lakehouse", "providers"])
def test_dependencies_point_toward_core_services(layer):
    forbidden = {"app", "tools", "evals", "data_pipeline", "tests"}
    if layer == "lakehouse":
        forbidden.update({"agentic_analytics.agent", "agentic_analytics.providers"})
    elif layer == "providers":
        forbidden.update({"agentic_analytics.agent", "agentic_analytics.lakehouse"})
    violations = []
    for path in (CORE / layer).rglob("*.py"):
        for module in imported_modules(path):
            if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    assert not violations, "\n".join(violations)
