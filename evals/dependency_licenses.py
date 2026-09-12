"""Record installed runtime dependency license metadata without network calls.

This is an inventory of package declarations and license file hashes, including
transitive dependencies. It does not infer a project's license from its APIs.
"""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]


def requirements(path):
    result = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("-r "):
            result.extend(requirements(path.parent / line[3:].strip()))
        elif line and not line.startswith("#"):
            result.append(Requirement(line))
    return result


def inventory():
    direct = requirements(ROOT / "requirements-app.txt")
    queue, packages = [requirement.name for requirement in direct], {}
    while queue:
        name = queue.pop(0)
        key = name.casefold().replace("_", "-")
        if key in packages:
            continue
        package = distribution(name)
        metadata = package.metadata
        licenses = []
        for file in package.files or []:
            if any(token in str(file).upper() for token in ("LICENSE", "COPYING", "NOTICE")):
                target = package.locate_file(file)
                if target.is_file():
                    licenses.append({"path": str(file), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        classifiers = [value for value in metadata.get_all("Classifier", []) if value.startswith("License ::")]
        packages[key] = {"name": metadata["Name"], "version": package.version,
                         "license_expression": metadata.get("License-Expression"),
                         "license_metadata": metadata.get("License"), "license_classifiers": classifiers,
                         "license_files": licenses, "project_urls": metadata.get_all("Project-URL", [])}
        for spec in package.requires or []:
            requirement = Requirement(spec)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                queue.append(requirement.name)
    return {"method": "Installed distribution metadata and license file hashes; runtime dependency closure for this Python/platform with no optional extras.",
            "direct_requirements": [str(requirement) for requirement in direct],
            "packages": [packages[key] for key in sorted(packages)],
            "frontend": json.loads((ROOT / "app/static/vendor/echarts-manifest.json").read_text()),
            "external_services": [{"name": "Kloudeks MIA", "role": "Competition-provided model endpoint, not a vendored software library; model access conditions are separate from this inventory."},
                                  {"name": "Public web search", "role": "Network data source; operator-configurable SearXNG or public search fallback. External service availability is not guaranteed by local open-source libraries."}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Recorded {len(result['packages'])} runtime packages and the bundled ECharts license manifest.")
