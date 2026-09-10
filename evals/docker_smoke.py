#!/usr/bin/env python3
"""Offline Docker integration smoke test for the dedicated codex-docker-pr3 project.

After building the dedicated Compose image, run from the repository root with
``python -m evals.docker_smoke``. The driver starts it on port 18870, creates smoke
workspaces, restarts/recreates that project, and restores a cold backup into
only codex-docker-pr3-restore on port 18871, resetting that disposable restore
volume each run. Leaves both test projects up.
Never contacts the native application on port 8870 or invokes a model provider.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid

from agentic_analytics.paths import REPO_ROOT

PROJECT = "codex-docker-pr3"
RESTORE_PROJECT = "codex-docker-pr3-restore"
RESTORE_PORT = 18871
SERVICE = "agent-app"
PORT = 18870
STORE = "/app/.lakehouse-runtime/app/lakehouse"


class SmokeError(RuntimeError):
    """A bounded check failed; messages deliberately omit raw process output."""


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


PUBLISH_UPLOAD = r'''
import json, sys
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore
workspace_id, source_id = sys.argv[1:]
store = LakehouseStore('/app/.lakehouse-runtime/app/lakehouse')
documents = DocumentTools(store, workspace_id, searxng_url=False)
inspection = documents.inspect_source(source_id=source_id)
contract = {'name': 'Docker smoke exact counts',
 'columns': {'date': {'dtype':'date','unit':'date','kind':'date','nullable':False},
             'count': {'dtype':'integer','unit':'count','kind':'count','nullable':False}},
 'key':['date'],'grain':['date'],'date_column':'date','frequency':'monthly'}
publication = documents.publish_selected_table(source_id, inspection['tables'][0]['table_id'], contract,
 expected_version=store.workspace(workspace_id)['version'], unit_evidence={'count':'count'})
service = LakehouseService(store, workspace_id)
metric = service.discover({'query':'count'})['metrics'][0]['metric_id']
analysis = service.execute({'start':'2025-01','end':'2025-01','frequency':'monthly',
 'columns':[{'name':'count','metric_id':metric}]})
print(json.dumps({'dataset_id':publication['dataset_id'],'analysis_id':analysis['analysis_id']}))
'''


class Driver:
    def __init__(self, lakehouse_dir, output):
        self.lakehouse_dir = lakehouse_dir.resolve()
        self.output = output
        self.base = f"http://127.0.0.1:{PORT}"
        self.env = {**os.environ, "MIA_API_KEY": "", "SEARXNG_URL": "",
                    "AGENT_PORT": str(PORT), "LAKEHOUSE_DIR": str(self.lakehouse_dir)}
        self.report = {"status": "running", "project": PROJECT, "port": PORT,
                       "started_at": datetime.now(timezone.utc).isoformat(),
                       "provider_calls": 0, "driver_sha256":digest(Path(__file__).read_bytes()), "checks": {}, "artifacts": {}}

    def command(self, args, *, input_text=None, timeout=240, environment=None):
        result = subprocess.run(args, cwd=REPO_ROOT, env=environment or self.env, input=input_text,
                                capture_output=True, text=True, timeout=timeout)
        require(result.returncode == 0, f"Command failed (exit {result.returncode}): {' '.join(args[:5])}")
        return result.stdout

    def compose_for(self, project, port, *args, **kwargs):
        require((project, port) in {(PROJECT, PORT), (RESTORE_PROJECT, RESTORE_PORT)}, "Compose scope rejected")
        return self.command(["docker", "compose", "-p", project, *args],
                            environment={**self.env, "AGENT_PORT":str(port)}, **kwargs)

    def compose(self, *args, **kwargs):
        return self.compose_for(PROJECT, PORT, *args, **kwargs)

    def container_python(self, code, *args):
        return json.loads(self.compose("exec", "-T", SERVICE, "python", "-", *args, input_text=code))

    def request(self, path, *, method="GET", value=None, data=None, headers=None, expected=200):
        headers = dict(headers or {})
        if value is not None:
            data = json.dumps(value).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=240) as response:
                status, body = response.status, response.read()
        except HTTPError as error:
            status, body = error.code, error.read()
        require(status == expected, f"Unexpected HTTP {status} for {method} {path.split('?')[0]}; expected {expected}")
        return body

    def json(self, path, **kwargs):
        return json.loads(self.request(path, **kwargs))

    def record(self, name, value=True):
        self.report["checks"][name] = value
        self.save()
        print(f"{name}: passed", flush=True)

    def save(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n")

    def wait_ready(self):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            try:
                status = self.json("/api/status")
                if status.get("status") == "ok":
                    require(status.get("provider_ready") is False, "Smoke service unexpectedly has a provider configured")
                    return
            except (URLError, ConnectionError, TimeoutError, SmokeError):
                pass
            time.sleep(1)
        raise SmokeError("Dedicated Compose service did not become ready")

    def safe_container_config(self, project=PROJECT, port=PORT):
        cid = self.compose_for(project, port, "ps", "-q", SERVICE).strip()
        require(cid and "\n" not in cid, "Expected one dedicated test service container")
        template = ('{"user":{{json .Config.User}},"read_only":{{json .HostConfig.ReadonlyRootfs}},'
                    '"ports":{{json .HostConfig.PortBindings}},"mounts":{{json .Mounts}},'
                    '"health":{{json .State.Health.Status}},'
                    '"image_id":{{json .Image}},"cap_drop":{{json .HostConfig.CapDrop}},"cap_add":{{json .HostConfig.CapAdd}},'
                    '"project":{{json (index .Config.Labels "com.docker.compose.project")}}}')
        config = json.loads(self.command(["docker", "inspect", "--format", template, cid]))
        require(config["project"] == project, "Container project mismatch")
        require(config["user"] == "10001:10001" and config["read_only"], "UID/read-only contract failed")
        require(config["cap_drop"] == ["ALL"] and not config["cap_add"], "Service capability policy changed")
        require(config["ports"] == {"8870/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}, "Unexpected port mapping")
        mounts = {m["Destination"]: {"type": m["Type"], "writable": m["RW"]} for m in config["mounts"]}
        require(mounts["/app/data_pipeline/lakehouse"] == {"type": "bind", "writable": False}, "Source mount must be read-only")
        require(mounts["/app/.lakehouse-runtime"] == {"type": "volume", "writable": True}, "Runtime must be a writable named volume")
        return {"uid": config["user"], "read_only": config["read_only"], "ports": config["ports"],
                "mounts": mounts, "health": config["health"], "image_id":config["image_id"],
                "container_id":cid, "cap_drop":config["cap_drop"], "cap_add":config["cap_add"], "runtime_volumes":[m["Name"] for m in config["mounts"] if m["Type"] == "volume"]}

    def compare_image_filesystems(self, main_id, restored_id):
        template = ('{"architecture":{{json .Architecture}},"layers":{{json .RootFS.Layers}},'
                    '"user":{{json .Config.User}},"cmd":{{json .Config.Cmd}},'
                    '"project_label":{{json (index .Config.Labels "com.docker.compose.project")}}}')
        images = [json.loads(self.command(["docker", "image", "inspect", "--format", template, image_id]))
                  for image_id in (main_id, restored_id)]
        require(all(images[0][key] == images[1][key] for key in ("architecture", "layers", "user", "cmd")),
                "Restored image differs in filesystem, architecture, USER or CMD")
        return {"architecture":images[0]["architecture"], "filesystem_layers_identical":True,
                "user_and_command_identical":True, "compose_project_labels":[item["project_label"] for item in images]}

    def snapshot(self, artifacts):
        wid, aid = artifacts["finance_workspace"], artifacts["revised_analysis"]
        gid, gaid, sid = artifacts["generic_workspace"], artifacts["integer_analysis"], artifacts["upload_source"]
        routes = {
            "finance_workspace": f"/api/workspaces/{wid}",
            "first_analysis": f"/api/workspaces/{wid}/analyses/{artifacts['first_analysis']}",
            "analysis": f"/api/workspaces/{wid}/analyses/{aid}",
            "chart": f"/api/workspaces/{wid}/analyses/{aid}/chart",
            "chart_artifact": f"/api/workspaces/{wid}/charts/{artifacts['chart']}",
            "explain": f"/api/workspaces/{wid}/analyses/{aid}/explain?" + urlencode({"column":"real_yoy_pct","period":"2026-06"}),
            "generic_workspace": f"/api/workspaces/{gid}",
            "integer_analysis": f"/api/workspaces/{gid}/analyses/{gaid}",
            "integer_explain": f"/api/workspaces/{gid}/analyses/{gaid}/explain?" + urlencode({"column":"count","period":"2025-01"}),
            "upload_inspection": f"/api/workspaces/{gid}/sources/{sid}",
        }
        result = {name: digest(json.dumps(self.json(path), sort_keys=True, ensure_ascii=False).encode()) for name, path in routes.items()}
        for name, path in {"csv":f"/api/workspaces/{wid}/analyses/{aid}/csv",
                           "integer_csv":f"/api/workspaces/{gid}/analyses/{gaid}/csv",
                           "upload_raw":f"/api/workspaces/{gid}/sources/{sid}/raw"}.items():
            result[name] = digest(self.request(path))
        return result

    def run(self):
        database = self.lakehouse_dir / "analytics.duckdb"
        require(database.is_file(), "Required source database is missing")
        source_hash = file_digest(database)
        self.report["source_database_sha256_before"] = source_hash
        self.compose("up", "-d", "--force-recreate", "--no-build", "--wait", "--wait-timeout", "90")
        self.wait_ready()
        self.record("container_config", self.safe_container_config())
        for path in ("/", "/static/app.js", "/static/charts.js", "/static/vendor/echarts.min.js"):
            require(bool(self.request(path)), "Empty static response")
        self.record("static_assets")
        self.request("/api/status", headers={"Host":"hostile.example"}, expected=400)
        self.request("/api/workspaces", method="POST", value={"profile":"generic"}, headers={"Origin":"https://hostile.example"}, expected=403)
        self.record("host_origin_rejection")
        suffix = uuid.uuid4().hex[:10]
        finance = self.json("/api/workspaces", method="POST", value={"name":"Docker smoke finance " + suffix,"profile":"finance"}, headers={"Origin":self.base})
        generic = self.json("/api/workspaces", method="POST", value={"name":"Docker smoke counts " + suffix,"profile":"generic"})
        require(finance["snapshot_id"] != generic["snapshot_id"], "Finance and generic snapshots should differ")
        require(not self.json(f"/api/workspaces/{generic['workspace_id']}/discover?query=count")["metrics"], "Generic workspace should begin without metrics")
        artifacts = {"finance_workspace":finance["workspace_id"],"generic_workspace":generic["workspace_id"],
                     "finance_snapshot":finance["snapshot_id"],"generic_snapshot":generic["snapshot_id"]}
        self.report["artifacts"].update(artifacts)
        self.record("finance_generic_and_same_origin")
        demo = json.loads(self.compose("exec", "-T", SERVICE, "python", "-m", "agentic_analytics.lakehouse.cli", "--store", STORE,
                                      "--workspace", finance["workspace_id"], "demo"))
        require(demo["status"] == "ok", "KOBI CLI demo failed")
        artifacts.update(first_analysis=demo["first"]["analysis_id"], revised_analysis=demo["revised"]["analysis_id"])
        analysis_path = f"/api/workspaces/{finance['workspace_id']}/analyses/{artifacts['revised_analysis']}"
        analysis = self.json(analysis_path)
        require(analysis["row_count"] == 66 and len(analysis["rows"]) == 66, "Expected complete 66-month KOBI analysis")
        require(analysis["rows"][0]["period"] == "2021-01" and analysis["rows"][-1]["period"] == "2026-06", "Unexpected period boundaries")
        require({"sme_credit","nominal_yoy_pct","cpi","real_sme_credit","real_yoy_pct"}.issubset(analysis["columns"]), "Revision lost expected columns")
        proof = self.json(analysis_path + "/explain?" + urlencode({"column":"real_yoy_pct","period":"2026-06"}))
        require(proof["value"] == analysis["rows"][-1]["real_yoy_pct"], "Evidence value differs from analysis")
        require(proof["source_files_verified"] is False, "Stored lineage must not claim original-file verification")
        require(len(self.request(analysis_path + "/csv").decode("utf-8-sig").splitlines()) == 67, "CSV row count mismatch")
        chart = self.json(analysis_path + "/chart", method="POST", value={"kind":"line","columns":["sme_credit","real_sme_credit"],"layout":"panels","title":"Docker KOBI growth and revision smoke"})
        require(chart["complete"] and chart["row_count"] == 66, "Chart must include complete data")
        artifacts["chart"] = chart["chart_id"]
        self.record("real_finance_demo", {"rows":66,"revised":True,"explain_value_matches":True,"raw_files_verified":False})
        raw = b"date,count\n2025-01,9007199254740993\n"
        boundary = "smoke" + uuid.uuid4().hex
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="counts.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()
                     + raw + f'\r\n--{boundary}--\r\n'.encode())
        upload = self.json(f"/api/workspaces/{generic['workspace_id']}/sources/upload", method="POST", data=multipart,
                           headers={"Content-Type":"multipart/form-data; boundary=" + boundary})
        artifacts["upload_source"] = upload["source_id"]
        require(self.request(f"/api/workspaces/{generic['workspace_id']}/sources/{upload['source_id']}/raw") == raw, "Upload raw bytes changed")
        publication = self.container_python(PUBLISH_UPLOAD, generic["workspace_id"], upload["source_id"])
        artifacts["integer_analysis"] = publication["analysis_id"]
        artifacts["integer_dataset"] = publication["dataset_id"]
        integer_path = f"/api/workspaces/{generic['workspace_id']}/analyses/{publication['analysis_id']}"
        expected = {"$integer":"9007199254740993"}
        require(self.json(integer_path)["rows"][0]["count"] == expected, "HTTP table integer precision was lost")
        integer_proof = self.json(integer_path + "/explain?" + urlencode({"column":"count","period":"2025-01"}))
        require(integer_proof["value"] == expected, "HTTP evidence integer precision was lost")
        require(b"9007199254740993" in self.request(integer_path + "/csv"), "CSV integer precision was lost")
        self.report["artifacts"].update(artifacts)
        self.record("upload_publication_exact_integer", {"raw_sha256":digest(raw),"table_explain_csv_exact":True})
        baseline = self.snapshot(artifacts)
        self.report["artifact_response_sha256"] = baseline
        self.compose("restart", "--timeout", "30", SERVICE)
        self.wait_ready()
        require(self.snapshot(artifacts) == baseline, "Artifacts changed after service restart")
        self.record("restart_persistence", {"responses_preserved":len(baseline)})
        self.compose("down", "--timeout", "30")
        self.compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
        self.wait_ready()
        require(self.snapshot(artifacts) == baseline, "Artifacts changed after Compose down/up")
        self.record("recreate_persistence", {"responses_preserved":len(baseline)})
        backup = self.output.parent / ("runtime-backup-" + suffix)
        require(not backup.exists(), "Backup destination must not already exist")
        self.compose("stop", "--timeout", "30", SERVICE)
        try:
            self.compose("cp", SERVICE + ":/app/.lakehouse-runtime", str(backup), timeout=600)
        finally:
            self.compose("start", SERVICE)
        self.wait_ready()
        backup_snapshot = backup / "app/lakehouse/snapshots" / artifacts["finance_snapshot"] / "data.duckdb"
        require(file_digest(backup_snapshot) == source_hash, "Cold backup finance snapshot differs")
        require((backup / "app/runs/runs.sqlite3").is_file(), "Cold backup omitted conversation store")
        backup_files = [path for path in backup.rglob("*") if path.is_file()]
        self.record("cold_backup", {"path":str(backup.relative_to(REPO_ROOT)) if backup.is_relative_to(REPO_ROOT) else str(backup), "files":len(backup_files),
                                  "bytes":sum(path.stat().st_size for path in backup_files), "snapshot_sha256":source_hash})
        # Only the dedicated restore project is disposable; the main volume is never removed.
        self.compose_for(RESTORE_PROJECT, RESTORE_PORT, "down", "--volumes", "--timeout", "30")
        self.compose_for(RESTORE_PROJECT, RESTORE_PORT, "create", "--build", SERVICE, timeout=600)
        self.compose_for(RESTORE_PROJECT, RESTORE_PORT, "cp", str(backup) + "/.", SERVICE + ":/app/.lakehouse-runtime/", timeout=600)
        self.compose_for(RESTORE_PROJECT, RESTORE_PORT, "run", "--rm", "--no-deps", "--user", "0:0", "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE",
                         "--entrypoint", "chown", SERVICE, "-R", "10001:10001", "/app/.lakehouse-runtime")
        self.compose_for(RESTORE_PROJECT, RESTORE_PORT, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
        original_base = self.base
        self.base = f"http://127.0.0.1:{RESTORE_PORT}"
        try:
            self.wait_ready()
            require(self.snapshot(artifacts) == baseline, "Restored project changed artifact IDs or values")
        finally:
            self.base = original_base
        self.record("cold_restore", {"project":RESTORE_PROJECT,"port":RESTORE_PORT,"responses_preserved":len(baseline),
                                     "config":self.safe_container_config(RESTORE_PROJECT, RESTORE_PORT)})
        final_hash = file_digest(database)
        self.report["source_database_sha256_after"] = final_hash
        require(source_hash == final_hash, "Read-only host database changed during smoke test")
        self.record("host_database_unchanged")
        self.record("final_container_config", self.safe_container_config())
        self.record("image_filesystem_match", self.compare_image_filesystems(
            self.report["checks"]["final_container_config"]["image_id"],
            self.report["checks"]["cold_restore"]["config"]["image_id"]))
        self.report["status"] = "passed"
        self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.report["service_left_running"] = True
        self.report["restore_service_left_running"] = True
        self.save()
        return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lakehouse-dir", type=Path, default=REPO_ROOT / "data_pipeline/lakehouse")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "tmp/docker-pr3/smoke-report.json")
    args = parser.parse_args()
    driver = Driver(args.lakehouse_dir, args.output)
    try:
        driver.run()
    except Exception as error:
        driver.report["status"] = "failed"
        driver.report["error"] = {"type":type(error).__name__, "message":str(error) if isinstance(error, SmokeError) else "Smoke driver failed; raw subprocess/environment output was not recorded."}
        driver.save()
        print("Docker smoke failed; see the sanitized report.", flush=True)
        return 1
    print(f"Docker smoke passed: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
