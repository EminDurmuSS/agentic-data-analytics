"""One bounded decision-maker over typed, durable analytics tools.

The LLM does not receive a database connection, arbitrary SQL, local filesystem
paths or a shell. Tool results are journaled before advancing the conversation.
"""
from __future__ import annotations

import copy
import json
import re
import time

import duckdb
import jsonschema

from agentic_analytics.agent.context import _compact, _model_tool_result, model_messages, workspace_context
from agentic_analytics.agent.delivery import _chart_confirmation, _requests_chart
from agentic_analytics.agent.run_store import AgentRunStore, canonical, fingerprint
from agentic_analytics.agent.tools.lakehouse import lakehouse_tools
from agentic_analytics.lakehouse.service import LakehouseService, PlanError, error_envelope
from agentic_analytics.providers.mia import MiaError


def _blocked(code, message):
    return {"status": "blocked", "errors": [{"code": code, "message": message}]}


def _normalize_result(result):
    """Preserve tool payloads while giving every failure one error contract."""
    if not isinstance(result, dict):
        raise PlanError("Tool result must be a JSON object")
    result = copy.deepcopy(result)
    if result.get("status") in {"blocked", "error", "failed", "unavailable"} and not result.get("errors"):
        code = result.get("code", "TOOL_UNAVAILABLE" if result["status"] == "unavailable" else "TOOL_FAILED")
        if code == "WRITE_OUTCOME_UNKNOWN":
            code = "UNKNOWN_MUTATION_OUTCOME"
        result["errors"] = [{"code": code, "message": result.get("message", "Tool could not complete the request.")}]
    return result


class AgentRuntime:
    def __init__(self, store, workspace_id, client, run_store: AgentRunStore,
                 extra_tools=None, *, max_decisions=10, max_repairs=2,
                 service=None, max_context_chars=75000, max_elapsed_seconds=240):
        if type(max_decisions) is not int or not 1 <= max_decisions <= 30 or type(max_repairs) is not int or not 0 <= max_repairs <= 5:
            raise ValueError("Invalid agent decision/repair budget")
        if type(max_context_chars) is not int or not 8000 <= max_context_chars <= 500000 or not 10 <= max_elapsed_seconds <= 3600:
            raise ValueError("Invalid context or elapsed-time budget")
        self.store, self.workspace_id, self.client, self.run_store = store, workspace_id, client, run_store
        self.service = service or LakehouseService(store, workspace_id)
        self.max_decisions, self.max_repairs, self.max_context_chars = max_decisions, max_repairs, max_context_chars
        self.max_elapsed_seconds = max_elapsed_seconds
        self.tools = self._tools()
        for name, definition in (extra_tools or {}).items():
            if name in self.tools or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                raise ValueError("Duplicate or invalid extra tool name")
            if not callable(definition.get("handler")) or definition.get("schema", {}).get("function", {}).get("name") != name:
                raise ValueError("Extra tool requires matching schema and callable handler")
            jsonschema.Draft202012Validator.check_schema(definition["schema"]["function"]["parameters"])
            self.tools[name] = dict(definition)

    def _tools(self):
        return lakehouse_tools(self.service)

    def _context(self, state):
        return workspace_context(self.store, self.workspace_id, state,
                                 max_decisions=self.max_decisions,
                                 charts_enabled="create_chart" in self.tools)

    def _messages(self, state):
        return model_messages(state, context_factory=self._context,
                              charts_enabled="create_chart" in self.tools,
                              max_context_chars=self.max_context_chars)

    def run(self, message, conversation_id=None, request_id=None):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 16000:
            raise ValueError("message must be a nonempty string of at most 16000 characters")
        with self.run_store.workspace_lock(self.workspace_id):
            record = self.run_store.start(self.workspace_id, message, conversation_id, request_id)
            return self._run(record)

    def resume(self, run_id):
        with self.run_store.workspace_lock(self.workspace_id):
            record = self.run_store.get(run_id)
            if record["workspace_id"] != self.workspace_id:
                raise ValueError("Run belongs to another workspace")
            return self._run(record)

    def _run(self, record):
        if record["result"] is not None:
            return record["result"]
        # Elapsed time is bounded per active invocation, using a monotonic clock.
        # Application downtime never consumes this budget. The total decision
        # count remains durable across every resume and is never reset here.
        invocation_started = time.monotonic()
        state, run_id = record["state"], record["run_id"]
        try:
            if "initial_candidates" not in state:
                # The model can refine this bounded natural-language search.
                state["initial_candidates"] = _model_tool_result("discover", self.service.discover({"query": record["message"][:300], "limit": 5}))
                self.run_store.event(run_id, "run_started", {"workspace_id": self.workspace_id, "conversation_id": record["conversation_id"], "request_id": record["request_id"]})
                self.run_store.checkpoint(run_id, state)
            while state["decisions"] < self.max_decisions or state["pending"]:
                if state["pending"]:
                    call = state["pending"][0]
                    result = self._dispatch(run_id, state, call)
                    state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_model_tool_result(call["function"]["name"], result))})
                    state["tool_results"].append({"tool": call["function"]["name"], "call_id": call["id"], "result": _compact(result)})
                    if self.tools.get(call["function"]["name"], {}).get("mutating") and result.get("status") == "ok":
                        write_key = fingerprint({"name": call["function"]["name"], "args": json.loads(call["function"]["arguments"])})
                        state.setdefault("successful_writes", {})[write_key] = result
                    state["pending"].pop(0)
                    if result.get("analysis_id") and result.get("status") in {"ok", "valid"}:
                        state["analysis_id"] = result["analysis_id"]
                        if call["function"]["name"] in {"execute", "revise_analysis", "query_grouped"}:
                            state["analysis_updated"] = True
                            if state.get("chart_analysis_id") != result["analysis_id"]:
                                state["chart_updated"] = False
                                state["chart_id"] = None
                                state["recommendations"] = []
                    if call["function"]["name"] == "create_chart" and result.get("status") == "ok":
                        state["chart_id"] = result.get("chart_id")
                        state["chart_analysis_id"] = result.get("analysis_id")
                        state["chart_updated"] = bool(result.get("chart_id"))
                        state["recommendations"] = result.get("recommendations", [])[:3]
                    failed = result.get("status") in {"blocked", "error", "failed", "unavailable"}
                    unresolved = state.setdefault("unresolved_errors", {})
                    tool_name = call["function"]["name"]
                    if failed:
                        unresolved[tool_name] = result.get("errors", [])
                    elif result.get("status") in {"ok", "valid"}:
                        unresolved.pop(tool_name, None)
                        if tool_name in {"execute", "revise_analysis", "query_grouped"}:
                            for resolved_name in ("execute", "revise_analysis", "query_grouped", "validate_plan"):
                                unresolved.pop(resolved_name, None)
                    for key in ("artifact_ref", "artifact_id", "source_id"):
                        if result.get(key):
                            item = {"kind": key, "id": result[key]}
                            if item not in state["artifacts"]:
                                state["artifacts"].append(item)
                    self.run_store.checkpoint(run_id, state)
                    if result.get("status") == "needs_input":
                        return self._finish(record, state, "needs_input", result["message"])
                    if result.get("status") in {"blocked", "error", "failed", "unavailable"}:
                        state["repairs"] += 1
                        self.run_store.checkpoint(run_id, state)
                        if state["repairs"] > self.max_repairs or any(e.get("code") in {"UNKNOWN_MUTATION_OUTCOME", "NO_PROGRESS"} for e in result.get("errors", [])):
                            return self._finish(record, state, "blocked", "Analiz güvenilir biçimde tamamlanamadı. Araç hata ayrıntıları kaydedildi.", errors=result.get("errors", []))
                    continue

                messages = self._messages(state)
                elapsed = time.monotonic() - invocation_started
                if elapsed >= self.max_elapsed_seconds:
                    return self._finish(record, state, "blocked", "Bu çalıştırmanın aktif süre sınırına ulaşıldı; kaydedilmiş sonuçlar korundu.", errors=[{"code": "TIME_BUDGET_EXCEEDED", "message": "No further provider request was started after this active invocation's deadline; the total decision budget remains durable."}])
                # Persist the budget debit before network I/O, so a crash cannot
                # reset provider-call limits or pretend a request was free.
                state["decisions"] += 1
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "model_request", {"decision": state["decisions"], "message_count": len(messages)})
                # The MIA live probe confirmed this server option avoids
                # exhausting the bounded output on private reasoning alone.
                response = self.client.chat(messages, tools=[v["schema"] for v in self.tools.values()], temperature=0, max_tokens=4096, enable_thinking=False)
                calls = response.get("tool_calls") or []
                content = response.get("content")
                usage = response.get("usage") or {}
                state["usage"].append(usage)
                self.run_store.event(run_id, "model_response", {"decision": state["decisions"], "finish_reason": response.get("finish_reason"), "tool_names": [c["function"]["name"] for c in calls], "usage": usage, "response_id": response.get("response_id"), "request_meta": response.get("request_meta", {})})
                if response.get("finish_reason") == "length":
                    state["repairs"] += 1
                    state["messages"].append({"role": "assistant", "content": "Önceki model çıktısı kesildi; hiçbir araç çalıştırılmadı."})
                    self.run_store.checkpoint(run_id, state)
                    if state["repairs"] > self.max_repairs:
                        return self._finish(record, state, "blocked", "Model çıktısı izin verilen uzunlukta tamamlanamadı.", errors=[{"code": "TRUNCATED_MODEL_OUTPUT", "message": "No truncated tool call was executed."}])
                    continue
                if calls:
                    state["messages"].append({"role": "assistant", "content": content, "tool_calls": copy.deepcopy(calls)})
                    state["pending"] = copy.deepcopy(calls)
                    self.run_store.checkpoint(run_id, state)
                    continue
                if isinstance(content, str) and content.strip():
                    if ("create_chart" in self.tools and _requests_chart(record["message"])
                            and not state.get("chart_updated") and not state.get("unresolved_errors")):
                        return self._finish(record, state, "partial" if state.get("analysis_updated") else "blocked",
                            "İstenen grafik kaydedilmedi; mevcut tablo korundu. Grafik oluşturma adımı tamamlanmalı.",
                            errors=[{"code": "CHART_NOT_CREATED", "message": "This turn requested a chart but produced no saved chart artifact."}])
                    if state.get("unresolved_errors"):
                        errors = [error for failures in state["unresolved_errors"].values() for error in failures]
                        if state.get("analysis_updated") or state.get("chart_updated"):
                            return self._finish(record, state, "partial", "Analiz sonucu kaydedildi; bazı araç adımları tamamlanamadı. Sonuç ve hata ayrıntıları birlikte sunuldu.", errors=errors)
                        return self._finish(record, state, "blocked", "İstenen işlem araçlar tarafından tamamlanamadı. Yeni bir analiz sonucu üretilmedi.", errors=errors)
                    content = _chart_confirmation(state, record["message"]) or content
                    state["messages"].append({"role": "assistant", "content": content})
                    return self._finish(record, state, "completed", content)
                state["repairs"] += 1
                state["messages"].append({"role": "assistant", "content": "Model boş yanıt verdi; geçerli bir araç çağrısı veya son cevap gerekiyor."})
                self.run_store.checkpoint(run_id, state)
                if state["repairs"] > self.max_repairs:
                    return self._finish(record, state, "blocked", "Model geçerli bir cevap üretmedi.", errors=[{"code": "EMPTY_MODEL_RESPONSE", "message": "No content or tool calls."}])
            if (state.get("analysis_updated") or state.get("chart_updated")) and not state.get("unresolved_errors"):
                return self._finish(record, state, "partial", "Analiz kaydedildi; son yanıtı üretme sınırına ulaşıldı. Tablo ve araç sonuçları hazır.", warnings=[{"code": "FINAL_RESPONSE_BUDGET_EXCEEDED", "message": "Verified analysis is available; no additional provider call was made for prose synthesis."}])
            return self._finish(record, state, "blocked", "Bu adımın model çağrı sınırına ulaşıldı; mevcut sonuçlar korundu.", errors=[{"code": "DECISION_BUDGET_EXCEEDED", "message": "Bounded agent decision budget reached."}])
        except MiaError as exc:
            return self._finish(record, state, "failed", str(exc), errors=[{"code": exc.code, "message": str(exc), "retryable": exc.retryable, "attempts": exc.attempts, "usage_unknown": True}])
        except (ValueError, OSError, duckdb.Error) as exc:
            error = error_envelope(exc)
            return self._finish(record, state, "blocked", "Çalışma alanı veya plan doğrulaması tamamlanamadı.", errors=error["errors"])

    def _expected_plan(self, name, args):
        if name == "execute":
            return copy.deepcopy(args)
        if name == "query_grouped":
            return {"query_type": "grouped", "request": copy.deepcopy(args)}
        if name == "revise_analysis":
            _, parent = self.store.load_analysis(args["analysis_id"])
            if parent.get("workspace_id") != self.workspace_id:
                raise PlanError("Analysis belongs to another workspace")
            if parent["plan"].get("query_type") == "grouped":
                raise PlanError("Grouped analysis cannot be revised with the scalar revision DSL", code="GROUPED_REVISION_UNSUPPORTED")
            plan = copy.deepcopy(parent["plan"])
            plan["columns"].extend(copy.deepcopy(args.get("add_columns", [])))
            plan.setdefault("operations", []).extend(copy.deepcopy(args.get("operations", [])))
            return plan
        return None

    def _recover(self, step, definition):
        intent, args, name = step["intent"], step["args"], step["name"]
        if definition.get("recover"):
            return definition["recover"](args, intent)
        if not definition.get("mutating"):
            return None
        if name not in {"execute", "revise_analysis", "query_grouped"}:
            return _blocked("UNKNOWN_MUTATION_OUTCOME", "Interrupted custom write has no reconciliation handler; it will not be repeated.")
        current = self.store.workspace(self.workspace_id)
        if current["revision_id"] == intent["input_revision"]:
            return None
        if current.get("analysis_head"):
            frame, manifest = self.store.load_analysis(current["analysis_head"])
            if manifest.get("workspace_revision_id") == intent["input_revision"] and manifest.get("plan") == intent["expected_plan"]:
                result = self.service._envelope(frame, manifest, [{"code": "RECOVERED_RESULT", "detail": "Previously committed result recovered after interruption; original transient warnings were not journaled."}])
                result["recovered"] = True
                return result
        return _blocked("UNKNOWN_MUTATION_OUTCOME", "Workspace changed after an interrupted tool; automatic replay is blocked.")

    def _dispatch(self, run_id, state, call):
        name = call["function"]["name"]
        step_id = f"{state['decisions']}:{call['id']}"
        definition = self.tools.get(name)
        if definition is None:
            result = _blocked("UNKNOWN_TOOL", "Requested tool is not registered.")
            self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
            return result
        try:
            raw_args = call["function"]["arguments"]
            if not isinstance(raw_args, str) or len(raw_args) > 48000:
                raise PlanError("Tool arguments exceed the request budget")
            args = json.loads(raw_args, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
            jsonschema.Draft202012Validator(definition["schema"]["function"]["parameters"]).validate(args)
            write_key = fingerprint({"name": name, "args": args})
            if definition.get("mutating") and write_key in state.get("successful_writes", {}):
                result = {**state["successful_writes"][write_key], "idempotent_replay": True}
                self.run_store.event(run_id, "tool_reused", {"tool": name, "call_id": call["id"], "result": result})
                return result
            step = self.run_store.step(run_id, step_id)
            if step:
                if step["args"] != args or step["name"] != name:
                    raise PlanError("Persisted call identity changed", code="CALL_ID_CONFLICT")
                if step["result"] is not None:
                    return step["result"]
                recovered = self._recover(step, definition)
                if recovered is not None:
                    recovered = _normalize_result(recovered)
                    self.run_store.complete_step(run_id, step_id, recovered)
                    self.run_store.event(run_id, "tool_recovered", {"tool": name, "call_id": call["id"], "result": recovered})
                    return recovered
            else:
                key = fingerprint({"name": name, "args": args, "revision": self.store.workspace(self.workspace_id)["revision_id"]})
                if state["seen"].get(key, 0) >= 2:
                    return _blocked("NO_PROGRESS", "The same tool request has already been attempted twice without a workspace change.")
                state["seen"][key] = state["seen"].get(key, 0) + 1
                current = self.store.workspace(self.workspace_id)
                intent = {"workspace_id": self.workspace_id, "input_revision": current["revision_id"], "workspace_version": current["version"], "expected_plan": self._expected_plan(name, args)}
                self.run_store.begin_step(run_id, step_id, name, args, intent)
                self.run_store.checkpoint(run_id, state)
                self.run_store.event(run_id, "tool_started", {"tool": name, "call_id": call["id"], "arguments": args})
            if name in {"execute", "revise_analysis"}:
                plan = step["intent"]["expected_plan"] if step else intent["expected_plan"]
                validation = self.service.validate_plan(plan)
                self.run_store.event(run_id, "plan_validation", {"tool": name, "call_id": call["id"], "plan": plan, "validation": validation})
                if validation.get("status") != "valid":
                    result = validation
                else:
                    result = definition["handler"](args)
            else:
                result = definition["handler"](args)
            result = _normalize_result(result)
            canonical(result)
        except jsonschema.ValidationError as exc:
            result = _blocked("INVALID_TOOL_ARGUMENTS", f"Tool arguments violate schema at {'.'.join(str(v) for v in exc.path) or '<root>'}.")
        except (ValueError, OSError, duckdb.Error) as exc:
            result = error_envelope(exc)
        # Only a persisted intent gets a persisted result. Invalid calls still
        # get an event and a matching tool response for provider round trips.
        if self.run_store.step(run_id, step_id):
            self.run_store.complete_step(run_id, step_id, result)
        self.run_store.event(run_id, "tool_result", {"tool": name, "call_id": call["id"], "result": result})
        return result

    def _finish(self, record, state, status, message, **extra):
        # Close every tool call before retaining history for the next user turn.
        for call in state["pending"]:
            state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": canonical(_blocked("TOOL_NOT_EXECUTED", "Run stopped before this call."))})
        state["pending"] = []
        if not state["messages"] or state["messages"][-1].get("role") != "assistant" or state["messages"][-1].get("content") != message:
            state["messages"].append({"role": "assistant", "content": message})
        workspace = self.store.workspace(self.workspace_id)
        result = {"run_id": record["run_id"], "conversation_id": record["conversation_id"], "request_id": record["request_id"],
                  "workspace_id": self.workspace_id, "status": status, "message": message,
                  "analysis_id": state.get("analysis_id"), "analysis_updated": state.get("analysis_updated", False),
                  "active_analysis_id": workspace.get("analysis_head"),
                  "chart_id": state.get("chart_id"), "chart_updated": state.get("chart_updated", False),
                  "recommendations": state.get("recommendations", []),
                  "artifacts": state["artifacts"], "tool_results": state["tool_results"],
                  "decisions": state["decisions"], "repairs": state["repairs"], "usage": state["usage"], **extra}
        self.run_store.finish(record["run_id"], state, result)
        self.run_store.event(record["run_id"], "run_finished", {"status": status, "analysis_id": result["analysis_id"], "decisions": state["decisions"]})
        return result
