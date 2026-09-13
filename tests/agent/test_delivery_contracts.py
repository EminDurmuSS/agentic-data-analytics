"""Executable delivery obligations, grounded numbers and interruption recovery.

Models are scripted; network responses are isolated fixtures. The parser,
publication, lakehouse calculations, summary and chart tools are real.
"""
import copy
import json
import re
from unittest.mock import patch

import duckdb
import pytest

from agentic_analytics.agent.run_store import AgentRunStore
from agentic_analytics.agent.context import _model_tool_result
from agentic_analytics.agent.runtime import AgentRuntime, _requests_shared_scale
from agentic_analytics.agent.schemas import obj
from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.documents import DocumentTools
from agentic_analytics.agent.tools.financial_import import FinancialImportTools
from agentic_analytics.agent.tools.datasets import DatasetTools
from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.store import LakehouseStore


def call(name, arguments, identifier=None):
    return {"content": None, "finish_reason": "stop", "tool_calls": [{
        "id": identifier or name, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}]}


def final(text="Sonuç hazır."):
    return {"content": text, "finish_reason": "stop", "tool_calls": []}


def result_of(messages, name):
    return next(json.loads(message["content"]) for message in reversed(messages)
                if message.get("role") == "tool" and message["tool_call_id"] == name)


class Client:
    def __init__(self, responses):
        self.responses, self.requests, self.options = list(responses), [], []

    def chat(self, messages, **options):
        self.requests.append(copy.deepcopy(messages))
        self.options.append(copy.deepcopy(options))
        assert self.responses, "Unexpected provider request"
        response = self.responses.pop(0)
        return response(messages) if callable(response) else response


@pytest.fixture
def env(tmp_path):
    database = tmp_path / "source.duckdb"
    with duckdb.connect(str(database)) as db:
        db.execute("CREATE SCHEMA catalog")
        db.execute("CREATE TABLE catalog.metric_bindings(metric_id VARCHAR,binding_json VARCHAR)")
        db.execute("CREATE TABLE observations(month VARCHAR,credit DOUBLE,profit DOUBLE)")
        db.executemany("INSERT INTO observations VALUES (?,?,?)", [
            ("2026-01", 100, 10), ("2026-02", 120, 15), ("2026-03", 150, 20), ("2026-04", 180, 25)])
        for name, kind in [("credit", "stock"), ("profit", "flow")]:
            binding = {"metric_id": name, "title": name, "source_system": "FIXTURE", "table": "observations",
                       "time_column": "month", "value_column": name, "filters": {}, "dimensions": {},
                       "native_frequency": "monthly", "kind": kind, "unit": "TRY", "scale": 1,
                       "currency": "TRY", "aggregation": "last" if kind == "stock" else "sum", "source_base": "",
                       "provenance_columns": [], "status": "ready", "notes": [], "contract_version": "test"}
            db.execute("INSERT INTO catalog.metric_bindings VALUES (?,?)", [name, json.dumps(binding)])
    store = LakehouseStore(tmp_path / "store")
    snapshot = store.publish_snapshot(database)
    workspace = store.create_workspace(snapshot["snapshot_id"])
    wid = workspace["workspace_id"]
    journal = AgentRunStore(tmp_path / "runs")
    extras = {**SummaryTools(store, wid).extra_tools(), **ChartTools(store, wid).extra_tools()}
    plan = {"start": "2026-01", "end": "2026-03", "frequency": "monthly",
            "columns": [{"name": "credit", "metric_id": "credit", "dimensions": {}}]}

    def runtime(responses, more=None, **kwargs):
        client = Client(responses)
        return AgentRuntime(store, wid, client, journal, extra_tools={**extras, **(more or {})}, **kwargs), client

    return store, wid, journal, plan, runtime


def test_final_numbers_come_from_saved_values_and_not_model_prose(env):
    store, wid, journal, plan, build = env
    runtime, client = build([call("execute", plan), final("Mart kredisi 999 TL ve büyüme yüzde 888.")])
    result = runtime.run("Kredi tablosunu göster")
    assert result["status"] == "completed"
    assert "150" in result["message"]
    assert "999" not in result["message"] and "888" not in result["message"]
    assert len(client.requests) == 2
    summaries = [step for step in result["tool_results"] if step["tool"] == "summarize_analysis"]
    assert len(summaries) == 1 and summaries[0]["automatic"]
    history = journal.get(result["run_id"])["state"]["messages"]
    assistant_prose = "\n".join(message.get("content") or "" for message in history
                                if message.get("role") == "assistant" and not message.get("tool_calls"))
    assert not re.search(r"(?<!\w)(?:999|888)(?!\w)", assistant_prose)
    assert store.workspace(wid)["version"] == 1


def test_table_claim_without_produced_result_is_not_completed(env):
    *_, build = env
    runtime, _ = build([final("Tablo hazır, 150 TL.")])
    result = runtime.run("Kredi tablosunu oluştur")
    assert result["status"] == "blocked"
    assert "TABLE_NOT_CREATED" in {error["code"] for error in result["errors"]}


@pytest.mark.parametrize("repair_full", [False, True])
def test_capacity_failure_cannot_drop_valid_difference_series_and_complete(env, repair_full):
    store, wid, journal, plan, build = env
    plan = copy.deepcopy(plan)
    plan["columns"] = [{**plan["columns"][0],"name":f"credit_{index}"} for index in range(3)]
    differences = [f"delta_{index}" for index in range(3)]
    plan["operations"] = [{"op":"difference","column":f"credit_{index}","output":name,"periods":1}
                          for index,name in enumerate(differences)]
    extras = ChartTools(store,wid).extra_tools()
    extras["create_chart"]["schema"] = copy.deepcopy(extras["create_chart"]["schema"])
    # Reproduce a provider-visible capacity smaller than a valid saved selection.
    # The real chart backend can render all three through its default selection.
    extras["create_chart"]["schema"]["function"]["parameters"]["properties"]["columns"]["maxItems"] = 2
    def chart(columns, identifier):
        return lambda messages: call("create_chart",{
            "analysis_id":result_of(messages,"execute")["analysis_id"],"kind":"bar",
            **({"columns":columns} if columns is not None else {})},identifier)
    responses = [call("plan_task",{"deliverables":["analysis","chart"]}),call("execute",plan),
                 chart(differences,"capacity-failure"),chart(differences[:2],"smaller-chart"),final()]
    if repair_full:
        responses.append(chart(None,"complete-chart"))
    responses.append(final())
    runtime, client = build(responses,more=extras,max_decisions=8)
    result = runtime.run("Her grubun fark sütununu grafikte göster",conversation_id="chart-coverage")
    record = journal.get(result["run_id"])
    assert record["state"]["chart_capacity_selections"][result["analysis_id"]] == differences
    assert result["status"] == ("completed" if repair_full else "partial")
    if repair_full:
        assert set(differences) <= set(record["state"]["chart_columns"])
        # Coverage of only the requested differences is sufficient. Their
        # original source columns are not added to the retained obligation.
        assert not runtime._chart_coverage_errors({**record["state"],"chart_columns":differences})
    else:
        error = next(error for error in result["errors"] if error["code"] == "CHART_SELECTION_INCOMPLETE")
        assert error["missing_columns"] == [differences[-1]]
    # A later explicit user subset starts a new scope and is legitimate.
    client.responses.extend([call("create_chart",{"analysis_id":result["analysis_id"],
        "kind":"bar","columns":differences[:2]},"new-turn-chart"),final()])
    later = runtime.run("Şimdi yalnız ilk iki fark sütununu grafikte göster",conversation_id="chart-coverage")
    assert later["status"] == "completed"
    assert not journal.get(later["run_id"])["state"].get("chart_capacity_selections")


def test_nonpublishing_import_outcome_is_rechecked_and_never_hides_a_later_refusal(env):
    _, _, journal, _, build = env
    attempts = []
    def ingest(arguments):
        attempts.append(arguments)
        if len(attempts) == 1:
            return {"status":"ok","import_status":"unsupported_layout","publication_performed":False}
        return {"status":"blocked","errors":[{"code":"SEMANTICS_REVIEW_REQUIRED",
                "message":"Updated source evidence requires review before publication."}]}
    schema = lambda name: {"type":"function","function":{
        "name":name,"description":"Fixture source tool","parameters":obj({},[])}}
    extras = {
        "ingest_source_table":{"schema":schema("ingest_source_table"),"handler":ingest,"mutating":True},
        "inspect_source":{"schema":schema("inspect_source"),"handler":lambda args:{"status":"ok"}},
    }
    runtime, _ = build([call("plan_task",{"deliverables":["dataset"]}),
        call("ingest_source_table",{},"first-import"),call("inspect_source",{}),
        call("ingest_source_table",{},"second-import"),final("Kaynak inceleme bekliyor.")],more=extras,max_decisions=5)
    result = runtime.run("Kaynak tablosunu içeri al")
    assert len(attempts) == 2
    assert result["status"] == "blocked"
    assert "SEMANTICS_REVIEW_REQUIRED" in {error["code"] for error in result["errors"]}
    assert not journal.get(result["run_id"])["state"].get("successful_writes")


def _source_workflow_tools(attempts, compiler_results):
    """Instrument the handler boundary; parser/publication integration is tested below."""
    def handler(name, args):
        attempts.append((name, copy.deepcopy(args)))
        if name == "ingest_source_table":
            return copy.deepcopy(compiler_results.pop(0))
        if name == "prepare_source_table":
            return {"status":"ok", "source_id":args["source_id"], "table_id":"prepared",
                    "preparation":{"source_table_id":args["table_id"]}}
        return {"status":"ok", "dataset_id":"fixture_dataset", "source_id":args["source_id"]}
    return {name:{"schema":{"type":"function","function":{"name":name,
                    "parameters":obj({"source_id":{"type":"string"},"table_id":{"type":"string"}},
                                     ["source_id","table_id"])}},
                  "handler":lambda args, name=name:handler(name,args),
                  "mutating":name != "prepare_source_table"}
            for name in ("ingest_source_table","prepare_source_table","publish_selected_table")}


def test_semantic_refusal_never_exposes_or_executes_low_level_publication(env):
    _,_,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[{"status":"blocked","code":"TABLE_REVIEW_REQUIRED", "message":"Review source cells"}])
    args={"source_id":"report","table_id":"unreviewed"}
    runtime,client=build([call("ingest_source_table",args),call("publish_selected_table",args),final()],more=tools)
    result=runtime.run("Kaynağı içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts]==["ingest_source_table"]
    assert "SOURCE_WORKFLOW_REQUIRED" in {error["code"] for error in result["errors"]}
    assert all("publish_selected_table" not in {tool["function"]["name"] for tool in request["tools"]}
               for request in client.options)
    assert not journal.get(result["run_id"])["state"].get("advanced_source_tables")


@pytest.mark.parametrize("other", [{"source_id":"another","table_id":"table_a"},
                                   {"source_id":"report","table_id":"table_b"}])
def test_unsupported_permission_is_exact_and_unrelated_publication_keeps_denial(env, other):
    _,_,journal,_,build=env
    attempts=[]
    unsupported={"status":"ok","import_status":"unsupported_layout","publication_performed":False}
    tools=_source_workflow_tools(attempts,[unsupported])
    args={"source_id":"report","table_id":"table_a"}
    runtime,_=build([call("ingest_source_table",args),call("prepare_source_table",other,"denied"),
                     call("publish_selected_table",args),final()],more=tools)
    result=runtime.run("Kaynakları içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts]==["ingest_source_table","publish_selected_table"]
    error=next(error for error in result["errors"] if error["code"]=="SOURCE_WORKFLOW_REQUIRED")
    assert {key:error[key] for key in other}==other
    assert journal.get(result["run_id"])["state"]["advanced_source_tables"]=={"report":{"table_a":"table_a"}}


def test_refusal_revokes_prepared_descendants_and_prevents_cached_write_replay(env):
    _,_,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[
        {"status":"ok","import_status":"unsupported_layout","publication_performed":False},
        {"status":"blocked","code":"SEMANTICS_REVIEW_REQUIRED","message":"New evidence requires review"}])
    args={"source_id":"report","table_id":"table_a"}
    prepared={**args,"table_id":"prepared"}
    runtime,client=build([call("ingest_source_table",args,"unsupported"),call("prepare_source_table",args),
        call("publish_selected_table",prepared,"published"),call("ingest_source_table",args,"review"),
        call("publish_selected_table",prepared,"denied-replay"),final()],more=tools)
    result=runtime.run("Kaynağı içeri al")
    assert result["status"]=="blocked"
    assert [name for name,_ in attempts].count("publish_selected_table")==1
    assert "publish_selected_table" not in {tool["function"]["name"] for tool in client.options[4]["tools"]}
    assert not journal.get(result["run_id"])["state"].get("advanced_source_tables")


def test_unsupported_receipt_recovers_capability_once_and_new_turn_resets_it(env):
    _,wid,journal,_,build=env
    attempts=[]
    tools=_source_workflow_tools(attempts,[{"status":"ok","import_status":"unsupported_layout","publication_performed":False}])
    args={"source_id":"report","table_id":"table_a"}
    class Crash(BaseException):
        pass
    complete=journal.complete_step
    def interrupt(run_id,step_id,result):
        complete(run_id,step_id,result)
        if result.get("import_status")=="unsupported_layout":
            raise Crash()
    runtime,_=build([call("ingest_source_table",args)],more=tools)
    with patch.object(journal,"complete_step",side_effect=interrupt),pytest.raises(Crash):
        runtime.run("Kaynağı içeri al",conversation_id="fallback",request_id="fallback-resume")
    pending=journal.find_request(wid,"fallback-resume")
    runtime,client=build([call("prepare_source_table",args),
        call("publish_selected_table",{**args,"table_id":"prepared"}),final()],more=tools)
    result=runtime.resume(pending["run_id"])
    assert result["status"]=="completed"
    assert [name for name,_ in attempts].count("ingest_source_table")==1
    assert journal.get(result["run_id"])["state"]["advanced_source_tables"]["report"]["prepared"]=="table_a"
    client.responses.extend([call("publish_selected_table",{**args,"table_id":"prepared"},"new-turn"),final()])
    later=runtime.run("Kaynağı yeniden içeri al",conversation_id="fallback")
    assert later["status"]=="blocked"
    assert "SOURCE_WORKFLOW_REQUIRED" in {error["code"] for error in later["errors"]}
    assert [name for name,_ in attempts].count("publish_selected_table")==1


def test_cross_scope_disclosure_comes_from_saved_warning_and_survives_chart_edit(env):
    import pandas as pd
    store,wid,_,_,build=env
    saved=store.save_analysis(wid,pd.DataFrame({"period":["2026-01","2026-02"],"comparison":[1.5,2.0]}),
        {},{"warnings":[{"code":"cross_scope_comparison","column":"comparison", "scope_reason":"Fabricated 999 claim"}]},
        schema={"period":{"kind":"dimension"},"comparison":{"dtype":"float64","unit":"percent","scale":1,"kind":"ratio"}},
        expected_version=0)
    runtime,client=build([final("Bu resmi pazar payı 999.")])
    result=runtime.run("Mevcut analizin sonucunu açıkla",conversation_id="scope-warning")
    assert result["status"]=="completed"
    assert "Kurum ve raporlama kapsamları farklıdır" in result["message"]
    assert "resmi sektör/pazar payı değildir" in result["message"] and "999" not in result["message"]
    client.responses.extend([call("create_chart",{"analysis_id":saved["analysis_id"],"kind":"line"}),final()])
    later=runtime.run("Çizgi grafiğini göster",conversation_id="scope-warning")
    assert later["status"]=="completed" and later["chart_updated"]
    assert "resmi sektör/pazar payı değildir" in later["message"] and "999" not in later["message"]


@pytest.mark.parametrize("text,expected", [
    ("Para birimi ve bin/milyon ölçeklerini eşitle, iki tutarı ve oranını hesapla", True),
    ("İki tutarı aynı para birimi ve ölçekte göster", True),
    ("Tutarları ortak ölçekte karşılaştır", True),
    ("Compare the amounts in the same currency and scale", True),
    ("Farklı ölçekleri aynı grafikte göster", False),
    ("Ölçeklerini eşitleme, özgün birimleri koru", False),
    ("Aynı ölçekte ne demek?", False),
    ("İki tutarı aynı ölçekte nasıl gösterebilirim?", False),
    ("How can I present amounts in the same scale?", False),
    ("Do not use the same scale", False),
])
def test_shared_scale_detector_is_an_explicit_command_boundary(text, expected):
    assert _requests_shared_scale(text) is expected


def _mixed_scale_plan(env, sector_currency="TRY"):
    """Replay the real rejected unit pairing through actual source publication."""
    store,wid,_,_,_=env
    docs=DocumentTools(store,wid)
    columns=[]
    for name,date,value,frequency,scale,currency in [
        ("company","2026-03-31",4783750292,"event",1000,"TRY"),
        ("sector","2026-03",49735194,"monthly",1000000,sector_currency)]:
        unit_quote=("thousand " if scale==1000 else "million ")+currency
        path=docs.upload_root/(name+".csv")
        path.write_text(f"date,value ({unit_quote})\n{date},{value}\n")
        source=docs.register_upload(path)
        table=docs.inspect_source(source_id=source["source_id"])["tables"][0]
        contract={"name":name,"frequency":frequency,"date_column":"date","key":["date"],"grain":["date"],
                  "columns":{"date":{"dtype":"date","unit":"calendar","kind":"dimension","nullable":False},
                             "value":{"dtype":"integer","unit":currency,"currency":currency,"scale":scale,"kind":"stock","nullable":False}}}
        result=docs.publish_selected_table(source["source_id"],table["table_id"],contract,store.workspace(wid)["version"],
            column_mapping=dict(zip(table["columns"],contract["columns"])),unit_evidence={"value":unit_quote})
        assert result["status"]=="ok",result
        columns.append({"name":name,"metric_id":f"overlay:{result['dataset_id']}:value",
                        "alignment":"period_end" if frequency=="event" else "native"})
    plan={"start":"2026-03","end":"2026-03","frequency":"monthly","columns":columns}
    if sector_currency=="TRY":
        plan["operations"]=[{"op":"ratio","column":"company","denominator":"sector","output":"comparison",
            "multiplier":100,"scope_policy":"explicit_comparison","scope_reason":"Explicit size comparison of different source populations"}]
    return plan


@pytest.mark.parametrize("repair", [False, True])
def test_actual_ratio_can_be_correct_while_common_scale_delivery_requires_saved_conversion(env, repair):
    store,wid,journal,_,build=env
    plan=_mixed_scale_plan(env)
    task={"deliverables":["analysis","chart"]}  # Actual failed run omitted normalization.
    def chart(messages):
        aid=result_of(messages,"execute")["analysis_id"]
        return call("create_chart",{"analysis_id":aid,"kind":"bar","columns":["comparison"]},"original-chart")
    def revise(messages):
        return call("revise_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "operations":[{"op":"scale","column":"company","output":"company_million","target_scale":1000000}]})
    def revised_chart(messages):
        return call("create_chart",{"analysis_id":result_of(messages,"revise_analysis")["analysis_id"],
            "kind":"bar","columns":["company_million","sector"]},"revised-chart")
    responses=[call("plan_task",task),call("execute",plan),chart,final("Tutarlar eşit ölçekte ve oran doğru.")]
    if repair:
        responses.extend([revise,revised_chart,final()])
    runtime,client=build(responses,max_decisions=len(responses))
    result=runtime.run("Para birimi ve bin/milyon ölçeklerini eşitle, iki tutarı ve oranını hesapla ve grafik göster")
    state=journal.get(result["run_id"])["state"]
    assert state["task_plan"]["normalization"]=={"same_unit_scale":True}
    original=next(item["result"] for item in result["tool_results"] if item["tool"]=="execute")
    old_frame,old_manifest=store.load_analysis(original["analysis_id"])
    assert old_frame.iloc[0]["comparison"]==pytest.approx(9.618441001758232)
    assert old_manifest["schema"]["company"]["scale"]==1000
    assert old_manifest["schema"]["sector"]["scale"]==1000000
    if repair:
        assert result["status"]=="completed",result
        current,manifest=store.load_analysis(result["analysis_id"])
        assert current.iloc[0]["company_million"]==pytest.approx(4783750.292)
        assert current.iloc[0]["company"]==4783750292
        assert manifest["schema"]["company_million"]["scale"]==manifest["schema"]["sector"]["scale"]==1000000
        assert state["chart_analysis_id"]==result["analysis_id"]!=original["analysis_id"]
        assert len(client.requests)==7 and state["delivery_repairs"]==1
    else:
        assert result["status"]=="partial"
        error=next(error for error in result["errors"] if error["code"]=="NORMALIZATION_NOT_SATISFIED")
        assert error["saved_units"]["company"]["scale"]==1000
        assert error["saved_units"]["sector"]["scale"]==1000000
        assert "revise_analysis" in error["message"]


def test_existing_explicit_scale_operations_satisfy_abstract_requirement_with_raw_columns_retained(env):
    _,_,journal,_,build=env
    plan=_mixed_scale_plan(env)
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("İki tutarı aynı para birimi ve ölçekte göster")
    assert result["status"]=="completed",result
    state=journal.get(result["run_id"])["state"]
    assert state.get("task_plan") is None
    assert state["request_normalization"]=={"same_unit_scale":True}


@pytest.mark.parametrize("repair", [False, True])
def test_prior_scaled_level_cannot_normalize_a_later_in_place_difference(env, repair):
    store,wid,journal,_,build=env
    plan={"start":"2026-02","end":"2026-03","frequency":"monthly",
          "columns":[{"name":"credit","metric_id":"credit"},{"name":"profit","metric_id":"profit"}],
          "operations":[
              {"op":"scale","column":"credit","output":"credit_thousand","target_scale":1000},
              {"op":"scale","column":"profit","output":"profit","target_scale":1000},
              {"op":"difference","column":"credit","output":"credit","periods":1}]}
    task={"deliverables":["analysis","chart"],"normalization":{
        "same_unit_scale":True,"columns":["credit","profit"]}}
    def chart(tool, columns, identifier):
        return lambda messages: call("create_chart",{
            "analysis_id":result_of(messages,tool)["analysis_id"],"kind":"bar","columns":columns},identifier)
    def revise(messages):
        return call("revise_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "operations":[{"op":"scale","column":"credit","output":"credit_change_thousand","target_scale":1000}]})
    responses=[call("execute",plan),call("plan_task",task),
               chart("execute",["credit","profit"],"original-chart"),final("Tutarlar aynı ölçekte.")]
    if repair:
        responses.extend([revise,chart("revise_analysis",["credit_change_thousand","profit"],"revised-chart"),final()])
    runtime,_=build(responses,max_decisions=len(responses))
    result=runtime.run("Kredi değişimi ile kârı aynı ölçekte karşılaştır ve grafikte göster")
    state=journal.get(result["run_id"])["state"]
    original=next(item["result"] for item in result["tool_results"] if item["tool"]=="execute")
    frame,manifest=store.load_analysis(original["analysis_id"])
    assert frame["credit"].tolist()==[20,30]
    assert frame["credit_thousand"].tolist()==pytest.approx([.12,.15])
    assert frame["profit"].tolist()==pytest.approx([.015,.02])
    assert manifest["schema"]["credit"]["scale"]==1
    assert manifest["schema"]["profit"]["scale"]==1000
    error=runtime._normalization_errors({"analysis_id":original["analysis_id"],"task_plan":task})[0]
    assert error["code"]=="NORMALIZATION_NOT_SATISFIED"
    assert "credit_thousand" not in error["saved_units"]  # The old level is a different quantity.
    if repair:
        assert result["status"]=="completed",result
        current,_=store.load_analysis(result["analysis_id"])
        assert current["credit_change_thousand"].tolist()==pytest.approx([.02,.03])
        assert current["credit"].tolist()==frame["credit"].tolist()
        assert state["chart_analysis_id"]==result["analysis_id"]!=original["analysis_id"]
        assert runtime._task_delivery_errors(state)==[]
        assert state["delivery_repairs"]==1
    else:
        assert result["status"]=="partial",result
        assert "NORMALIZATION_NOT_SATISFIED" in {item["code"] for item in result["errors"]}
        assert "NORMALIZATION_NOT_SATISFIED" in {item["code"] for item in runtime._task_delivery_errors(state)}


def test_single_date_delivery_has_each_presented_amount_once_and_sources_without_technical_boilerplate(env):
    from agentic_analytics.agent.delivery import _analysis_confirmation, _cell_confirmation
    store,wid,journal,_,build=env
    plan=_mixed_scale_plan(env)
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    def explain(column, identifier):
        def response(messages):
            return call("explain_value",{"analysis_id":result_of(messages,"execute")["analysis_id"],"column":column,"period":"2026-03"},identifier)
        return response
    runtime,_=build([call("execute",plan),explain("company","company-proof"),explain("sector","sector-proof"),final()])
    result=runtime.run("İki tutarı ortak ölçekte ve kaynaklarıyla göster")
    assert result["status"]=="completed"
    assert result["message"].count("4.783.750,292")==1
    assert result["message"].count("49.735.194")==1
    assert result["message"].count("9,618441")==1
    assert "4.783.750.292" not in result["message"]
    assert "company_million" not in result["message"] and "source_financial_facts" not in result["message"]
    assert "ilk değer" not in result["message"].casefold() and "son değer" not in result["message"].casefold()
    assert "bayt" not in result["message"] and "Kaynaklar:" in result["message"]
    state=copy.deepcopy(journal.get(result["run_id"])["state"])
    for item in state["tool_results"]:
        if item["tool"]=="summarize_analysis":
            item.pop("automatic",None)  # Durable interruption recovery uses the reserved call id.
    assert _analysis_confirmation(store,wid,state).count("4.783.750,292")==1
    proof=next(item["result"] for item in state["tool_results"] if item.get("call_id")=="company-proof")
    proof["lineage"]["document_provenance"].update(source_url="https://reports.example.org/statement.pdf",page=11)
    assert "https://reports.example.org/statement.pdf#page=11" in _cell_confirmation(store,wid,state)
    assert "s. 11" in _cell_confirmation(store,wid,state)


def test_explicit_grouped_summary_preserves_requested_totals_and_missing_warning(env):
    from agentic_analytics.agent.delivery import _analysis_confirmation
    import pandas as pd
    store,wid,_,_,_=env
    frame=pd.DataFrame({"period":["2026-01","2026-02","2026-01","2026-02"],"bank":["A","A","B","B"],"profit":[10,15,20,None]})
    schema={"profit":{"kind":"flow","status":"ready","unit":"TRY","currency":"TRY","scale":1,"additive_over_time":True}}
    lineage={"group_by":"bank","frequency":"monthly","groups":{json.dumps(group):{"sources":{"profit":{
        "binding":{"title":"Dönem kârı","dimension_labels":{"bank":{"A":"Grup A","B":"Grup B"}}},"dimensions":{"bank":group}}}} for group in ["A","B"]}}
    saved=store.save_analysis(wid,frame,{"frequency":"monthly"},lineage,schema=schema,expected_version=0)
    summary=SummaryTools(store,wid).summarize_analysis(saved["analysis_id"],statistics=["sum"])
    text=_analysis_confirmation(store,wid,{"analysis_id":saved["analysis_id"],"analysis_updated":True,
        "tool_results":[{"tool":"summarize_analysis","call_id":"requested-total","result":summary}]})
    assert "Grup A" in text and "Grup B" in text
    assert "25 TL" in text and "hesaplanamadı" in text and "eksik" in text.casefold()
    assert "Grup A) (Grup B" not in text


def test_unrequested_scale_conversion_is_not_enforced(env):
    *_,build=env
    plan=_mixed_scale_plan(env)
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("Özgün kaynak tutarlarını ve oranını göster")
    assert result["status"]=="completed",result


def test_common_scale_requirement_never_authorizes_implicit_currency_conversion(env):
    store,_,_,_,build=env
    plan=_mixed_scale_plan(env,sector_currency="USD")
    runtime,_=build([call("execute",plan),final()],max_decisions=2)
    result=runtime.run("Tutarları aynı para birimi ve ölçekte göster")
    assert result["status"]=="partial"
    error=next(error for error in result["errors"] if error["code"]=="NORMALIZATION_NOT_SATISFIED")
    assert error["currency_conversion_required"] and "not authorized" in error["message"]
    _,manifest=store.load_analysis(result["analysis_id"])
    assert manifest["schema"]["company"]["currency"]=="TRY" and manifest["schema"]["sector"]["currency"]=="USD"


def test_common_scale_contract_cannot_discard_columns_or_count_scaled_copy_twice(env):
    _,_,journal,_,build=env
    plan=_mixed_scale_plan(env)
    task={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["company","sector"]}}
    runtime,_=build([call("execute",plan),call("plan_task",task,"original"),call("plan_task",{
        "deliverables":["analysis"],"normalization":{"same_unit_scale":True}},"weaken")],max_repairs=0)
    result=runtime.run("Tutarları ortak ölçekte göster")
    assert result["status"]=="partial" and result["errors"][0]["code"]=="TASK_PLAN_LOCKED"
    assert journal.get(result["run_id"])["state"]["task_plan"]==task
    plan["operations"].insert(0,{"op":"scale","column":"company","output":"company_million","target_scale":1000000})
    duplicated={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["company","company_million"]}}
    runtime,_=build([call("execute",plan),call("plan_task",duplicated)],max_decisions=2,max_repairs=0)
    result=runtime.run("İki tutarı ortak ölçekte karşılaştır")
    assert result["status"]=="partial"
    assert "TASK_PLAN_INVALID_NORMALIZATION" in {error["code"] for error in result["errors"]}
    assert "NORMALIZATION_DUPLICATE_SOURCE" in result["errors"][0]["message"]


def test_normalization_does_not_lock_guessed_columns_before_analysis(env):
    _,_,journal,_,build=env
    task={"deliverables":["analysis"],"normalization":{"same_unit_scale":True,"columns":["guessed_a","guessed_b"]}}
    runtime,_=build([call("plan_task",task)],max_decisions=1,max_repairs=0)
    result=runtime.run("Tutarları ortak ölçekte göster")
    assert result["errors"][0]["code"]=="TASK_PLAN_REQUIRES_ANALYSIS"
    assert not journal.get(result["run_id"])["state"].get("task_plan")


def test_numeric_followup_reads_saved_analysis_without_mutating_or_trusting_prose(env):
    store, wid, journal, plan, build = env
    runtime, _ = build([call("execute", plan), final()])
    first = runtime.run("Kredi tablosunu göster")
    before = store.workspace(wid)
    runtime, client = build([final("Son kredi 999 TL oldu.")])
    result = runtime.run("Mevcut tablonun son değerini açıkla", conversation_id=first["conversation_id"])
    assert result["status"] == "completed"
    assert "150" in result["message"] and "999" not in result["message"]
    assert result["tool_results"] == [] and not result["analysis_updated"]
    assert store.workspace(wid) == before
    assert len(client.requests) == 1
    assert "999" not in journal.get(result["run_id"])["state"]["messages"][-1]["content"]


@pytest.mark.parametrize("repairs", [True, False])
def test_confident_dataset_numeric_answer_requires_tool_evidence(env, repairs):
    _, _, _, plan, build = env
    responses = [final("credit 999 TL")]
    responses += [call("execute", plan), final()] if repairs else [final("credit 999 TL")]
    runtime, client = build(responses, max_decisions=3 if repairs else 2)
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("credit")
    assert "999" not in result["message"]
    if repairs:
        assert result["status"] == "completed" and "150" in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "NUMERICAL_EVIDENCE_MISSING" in {error["code"] for error in result["errors"]}
    assert len(client.requests) == len(responses)


def test_educational_numeric_example_is_labeled_and_not_mistaken_for_source_data(env):
    *_, build = env
    runtime, _ = build([call("plan_task", {"deliverables":["explanation"]}), final("Varsayımsal 100 TL ve 120 TL iki örnek değerdir.")])
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("Kredi stok kavramını varsayımsal sayılarla açıkla")
    assert result["status"] == "completed"
    assert "100" in result["message"] and "kaynak verilerden hesaplanmış bir sonuç değildir" in result["message"]
    assert not result["analysis_updated"]


@pytest.mark.parametrize("repairs", [True, False])
def test_metadata_description_cannot_ground_an_invented_observation(env, repairs):
    _, _, _, plan, build = env
    responses = [call("describe", {"metric_id":"credit"}), final("credit 999 TL")]
    responses += [call("execute", plan), final()] if repairs else [final("credit 999 TL")]
    runtime, client = build(responses, max_decisions=len(responses))
    runtime.service.discover = lambda args: {"status":"ok","metrics":[{"metric_id":"credit","title":"credit","status":"ready"}]}
    result = runtime.run("credit")
    assert "999" not in result["message"]
    if repairs:
        assert result["status"] == "completed" and "150" in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "NUMERICAL_EVIDENCE_MISSING" in {error["code"] for error in result["errors"]}
    assert len(client.requests) == len(responses)


def test_malformed_json_tool_history_is_sanitized_only_for_provider_retry(env):
    _, _, journal, plan, build = env
    malformed = call("describe", {})
    malformed["tool_calls"][0]["function"]["arguments"] = '{"metric_id":'
    def repair(messages):
        for message in messages:
            for item in message.get("tool_calls", []):
                assert isinstance(json.loads(item["function"]["arguments"]),dict)
        assert result_of(messages,"describe")["errors"][0]["code"] == "INVALID_TOOL_ARGUMENTS"
        return call("describe",{"metric_id":"credit"},"describe-fixed")
    runtime, _ = build([malformed,repair,call("execute",plan),final()])
    result = runtime.run("Kredi tablosunu göster")
    assert result["status"] == "completed"
    calls = [item for message in journal.get(result["run_id"])["state"]["messages"] for item in message.get("tool_calls", [])]
    assert calls[0]["function"]["arguments"] == '{"metric_id":'


def test_fatal_tool_failure_keeps_grounded_partial_analysis_receipt(env):
    _, _, _, plan, build = env
    invalid = {**plan,"operations":[{"op":"deflate","column":"credit","index":"credit","output":"bad","base_period":"2026-01"}]}
    runtime, _ = build([call("execute",plan,"good"),call("execute",invalid,"bad")],max_repairs=0)
    result = runtime.run("Hesapla")
    assert result["status"] == "partial" and result["analysis_updated"]
    assert "150" in result["message"]
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}


def test_ask_user_after_analysis_cannot_bypass_requested_chart(env):
    _, _, _, plan, build = env
    runtime, _ = build([call("execute", plan), call("ask_user", {"question": "Başka bir şey ister misiniz?"})])
    result = runtime.run("Kredi tablosunu oluştur ve çizgi grafik çiz")
    assert result["status"] == "partial"
    assert result["analysis_updated"] and not result["chart_updated"]
    assert "CHART_NOT_CREATED" in {error["code"] for error in result["errors"]}


def test_ask_user_pending_calls_are_closed_before_automatic_summary(env):
    _, _, journal, plan, build = env
    question = call("ask_user", {"question": "Başka bir dönem de gerekli mi?"})
    question["tool_calls"].extend(call("describe", {"metric_id": "credit"}, "unused")["tool_calls"])
    runtime, _ = build([call("execute", plan), question])
    result = runtime.run("Kredi tablosunu göster")
    history = journal.get(result["run_id"])["state"]["messages"]
    pending = set()
    for message in history:
        if message.get("role") == "assistant":
            assert not pending, "An assistant message orphaned an earlier tool call"
            pending.update(tool["id"] for tool in message.get("tool_calls", []))
        elif message.get("role") == "tool":
            pending.remove(message["tool_call_id"])
    assert not pending


def test_automatic_summary_recovers_after_interruption_without_provider_replay(env):
    _, _, journal, plan, build = env

    class Crash(BaseException):
        pass

    original = journal.complete_step
    def interrupt(run_id, step_id, result):
        if "runtime_summary_" in step_id:
            raise Crash()
        return original(run_id, step_id, result)

    runtime, client = build([call("execute", plan), final("Untrusted 999.")])
    with patch.object(journal, "complete_step", side_effect=interrupt), pytest.raises(Crash):
        runtime.run("Kredi tablosunu göster", request_id="interrupted-summary")
    pending = journal.find_request(env[1], "interrupted-summary")
    assert pending["state"]["delivery_pending"]
    runtime, resumed_client = build([])
    result = runtime.resume(pending["run_id"])
    assert result["status"] == "completed"
    assert result["decisions"] == 2 and len(client.requests) == 2
    assert not resumed_client.requests
    assert "150" in result["message"] and "999" not in result["message"]


def web_tool(handler):
    return {"research_web": {"schema": {"type": "function", "function": {"name": "research_web",
            "parameters": obj({"query": {"type": "string"}})}}, "handler": handler}}


def test_web_failure_can_recover_and_success_does_not_disable_tools(env):
    _, _, _, plan, build = env
    attempts = []
    def research(args):
        attempts.append(args["query"])
        if len(attempts) == 1:
            return {"status": "unavailable", "code": "NO_READABLE_SOURCES", "message": "No readable source"}
        return {"status": "ok", "sources": [{"url": "https://example.org/report", "title": "Report", "content": "2026 report."}]}
    runtime, client = build([
        call("research_web", {"query": "first"}, "first"), call("research_web", {"query": "refined"}, "second"),
        call("execute", plan), lambda messages: call("create_chart", {
            "analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"}), final()], more=web_tool(research))
    result = runtime.run("Kaynak araştır, kredi tablosunu oluştur ve çizgi grafik çiz")
    assert result["status"] == "completed" and result["chart_updated"]
    assert attempts == ["first", "refined"]
    assert all(option["tools"] for option in client.options)


def test_web_source_cannot_erase_failed_calculation(env):
    _, _, _, plan, build = env
    invalid = {**plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2026-01", "output": "real"}]}
    research = lambda args: {"status": "ok", "sources": [{"url": "https://example.org/report", "content": "Report available."}]}
    runtime, _ = build([call("execute", invalid), call("research_web", {"query": "report"}), final()], more=web_tool(research))
    result = runtime.run("Analiz yap ve kaynak bul")
    assert result["status"] == "partial"
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}
    assert not result["analysis_updated"]


def test_source_backed_ownership_answer_keeps_complete_list_and_recommendation(env):
    store, wid, journal, plan, build = env
    # A follow-up can research ownership while an earlier quantitative analysis
    # remains active; it must not be replaced by that analysis or a page excerpt.
    previous, _ = build([call("execute", plan), final()])
    prior = previous.run("Kredi tablosunu göster")
    names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota"]
    ownership = {"url": "https://example.org/shareholders", "title": "Example ortaklık yapısı",
                 "content": "Example ortakları. " + "Kaynak açıklaması. " * 40 + ", ".join(names)}
    cover = {"url": "https://example.org/report.pdf", "title": "Report",
             "content": "Financial report cover", "tables": [{"columns": ["Cover"], "preview": [{"Cover": None}]}]}
    response = ("Example ortakları: " + ", ".join(names) + ". "
                "[Resmi ortaklık sayfası](https://example.org/shareholders)\n\n"
                "Karşılaştırmayı Alpha ve Beta ile genişletmeyi öneriyorum. "
                "Her biri için aynı dönem ve konsolidasyon kapsamındaki raporu inceleyelim.")
    runtime, _ = build([call("research_web", {"query": "Example shareholders"}), final(response)],
                       more=web_tool(lambda args: {"status": "ok", "sources": [ownership, cover]}))
    result = runtime.run("Example'nin ortakları kimler, karşılaştırmaya hangilerini ekleyelim?",
                         conversation_id=prior["conversation_id"])
    assert result["status"] == "completed"
    assert result["message"].startswith(response)
    assert all(name in result["message"] for name in names)
    assert result["message"].count(ownership["url"]) == 1
    assert "Kaynak metninden" not in result["message"] and "None" not in result["message"]
    assert "150" not in result["message"] and not result["analysis_updated"]
    assert store.workspace(wid)["analysis_head"] == prior["analysis_id"]
    assert result["tool_results"][0]["result"]["sources"][1]["tables"] == cover["tables"]
    delivered = journal.get(result["run_id"])["state"]["messages"][-1]["content"]
    assert response in delivered


@pytest.mark.parametrize("repair", [True, False])
def test_unsolicited_ownership_percentages_are_repaired_or_not_delivered(env, repair):
    _, _, journal, _, build = env
    source = {"url": "https://example.org/owners", "title": "Example ortakları",
              "content": "Example ortakları: Alpha yüzde 9,09; Beta yüzde 18,18."}
    bad = "Example ortakları Alpha (%9,09) ve Beta (%18,18). Alpha'yı öneririm: en büyük ortaklardan (%18,18)."
    corrected = ("Example ortakları Alpha ve Beta. Alpha'nın mevcut analizle aynı dönem ve konsolidasyon "
                 "kapsamındaki raporunu bularak karşılaştırmaya eklemeyi öneriyorum.")
    runtime, client = build([call("research_web", {"query": "Example shareholders"}), final(bad),
                            final(corrected if repair else bad)],
                            more=web_tool(lambda args: {"status": "ok", "sources": [source]}))
    result = runtime.run("Example'nin ortakları kimler, hangisini karşılaştırmaya ekleyelim?")
    assert len(client.requests) == 3
    assert "UNSOLICITED_OWNERSHIP_PERCENTAGES" in json.dumps(client.requests[2])
    assert "9,09" not in result["message"] and "18,18" not in result["message"]
    assert result["tool_results"][0]["result"]["sources"][0] == source
    history = journal.get(result["run_id"])["state"]["messages"]
    delivered_prose = [item["content"] for item in history if item.get("role") == "assistant" and not item.get("tool_calls")]
    assert bad not in delivered_prose
    if repair:
        assert result["status"] == "completed"
        assert result["message"].startswith(corrected)
        assert source["url"] in result["message"]
    else:
        assert result["status"] == "blocked"
        assert "UNSOLICITED_OWNERSHIP_PERCENTAGES" in {error["code"] for error in result["errors"]}


@pytest.mark.parametrize("question", ["Example'nin ortakları ve payları nedir?", "Who are Example's shareholders and their percentages?"])
def test_requested_ownership_percentages_are_not_silently_removed(env, question):
    *_, build = env
    source = {"url": "https://example.org/owners", "title": "Example ortakları",
              "content": "Example shareholders: Alpha 40%, Beta 60%."}
    response = "Example ortakları Alpha (%40) ve Beta (%60)."
    runtime, client = build([call("research_web", {"query": "Example shareholders"}), final(response)],
                           more=web_tool(lambda args: {"status": "ok", "sources": [source]}))
    result = runtime.run(question)
    assert result["status"] == "completed" and result["message"].startswith(response)
    assert len(client.requests) == 2


def test_percent_encoded_citation_and_nonownership_analysis_do_not_trigger_ownership_gate(env):
    from agentic_analytics.agent.runtime import _ownership_percentage_errors, _requests_ownership_percentages
    state = {"external_facts_required": True, "ownership_percentages_requested": False}
    assert not _ownership_percentage_errors(state, "[Ortaklar](https://example.org/ortak%20listesi?name=%25)")
    assert not _ownership_percentage_errors({"external_facts_required": False}, "Büyüme %20.")
    assert not _requests_ownership_percentages("Example'nin ortaklarını paylaşır mısın? https://example.org/ortak%20listesi")
    for claim in ("Alpha yüzde 18,18", "Alpha 18.18 percent", "Alpha %18,18", "Alpha 18,18%"):
        assert _ownership_percentage_errors(state, claim)[0]["code"] == "UNSOLICITED_OWNERSHIP_PERCENTAGES"


def test_research_links_cannot_replace_verified_arithmetic_with_model_claims(env):
    _, _, _, plan, build = env
    research = lambda args: {"status": "ok", "sources": [{
        "url": "https://example.org/report", "title": "Report", "content": "Model sees unrelated 777 TL here."}]}
    runtime, _ = build([call("research_web", {"query": "report"}), call("execute", plan),
                         final("Kredi 999 TL, büyüme yüzde 888.")], more=web_tool(research))
    result = runtime.run("Kaynak bul ve kredi tablosunu göster")
    assert result["status"] == "completed"
    assert "150" in result["message"]
    assert all(value not in result["message"] for value in ("777", "888", "999"))
    assert "[Report](https://example.org/report)" in result["message"]


def test_inspected_source_answer_has_citation_without_research_wrapper(env):
    *_, build = env
    tools = {"inspect_source": {"schema": {"type": "function", "function": {"name": "inspect_source",
             "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
             "status": "ok", "source_id": "source", "source_url": args["url"],
             "text": "Example ortakları: Alpha ve Beta.", "title": "Example ortaklık yapısı"}}}
    response = "Example ortakları Alpha ve Beta. Karşılaştırmaya Alpha ile başlamayı öneriyorum."
    runtime, _ = build([call("inspect_source", {"url": "https://example.org/shareholders"}), final(response)], more=tools)
    result = runtime.run("Example'nin ortakları kimler ve hangisini ekleyelim?")
    assert result["status"] == "completed"
    assert result["message"].startswith(response)
    assert "[Example ortaklık yapısı](https://example.org/shareholders)" in result["message"]


@pytest.mark.parametrize("read_status,source_text,completes", [
    ("ok", "Example ortakları: Alpha ve Beta.", True),
    ("ok", "OtherCo shareholders are Alpha and Beta.", False),
    ("ok", "Example total assets report.", False),
    ("blocked", "", False),
])
def test_failed_research_is_replaced_only_by_relevant_ownership_read(env, read_status, source_text, completes):
    *_, build = env
    tools = web_tool(lambda args: {"status": "blocked", "code": "NO_READABLE_SOURCES", "message": "No readable result"})
    tools["inspect_source"] = {"schema": {"type": "function", "function": {"name": "inspect_source",
        "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
            "status": read_status, "source_id": "source", "text": source_text,
            "source_url": "https://otherco.org/shareholders" if "OtherCo" in source_text else args["url"],
            **({"code": "SOURCE_NOT_FOUND", "message": "Unavailable"} if read_status != "ok" else {})}}
    response = "Example ortakları Alpha ve Beta. Karşılaştırmaya Alpha ile başlamayı öneriyorum."
    runtime, _ = build([call("research_web", {"query": "Example shareholders"}),
        call("inspect_source", {"url": "https://example.org/shareholders"}), final(response)], more=tools, max_decisions=3)
    result = runtime.run("Example'nin ortakları kimler, hangisini ekleyelim?")
    errors = {error["code"] for error in result.get("errors", [])}
    if completes:
        assert result["status"] == "completed"
        assert result["message"].startswith(response)
        assert "https://example.org/shareholders" in result["message"]
        assert not errors
    else:
        assert result["status"] == "blocked"
        assert {"NO_READABLE_SOURCES", "EXTERNAL_FACTS_UNVERIFIED"} <= errors
        assert "Alpha" not in result["message"]
    # Reconciliation changes live error state, never the original evidence.
    assert result["tool_results"][0]["result"]["errors"][0]["code"] == "NO_READABLE_SOURCES"


def test_research_replacement_read_does_not_clear_calculation_error(env):
    _, _, _, plan, build = env
    invalid = {**plan, "operations": [{"op": "deflate", "column": "credit", "index": "credit", "base_period": "2026-01", "output": "real"}]}
    tools = web_tool(lambda args: {"status": "blocked", "code": "NO_READABLE_SOURCES", "message": "Unavailable"})
    tools["inspect_source"] = {"schema": {"type": "function", "function": {"name": "inspect_source",
        "parameters": obj({"url": {"type": "string"}})}}, "handler": lambda args: {
            "status": "ok", "source_id": "source", "text": "Report read.", "source_url": args["url"]}}
    runtime, _ = build([call("execute", invalid), call("research_web", {"query": "report"}),
        call("inspect_source", {"url": "https://example.org/report"}), final()], more=tools)
    result = runtime.run("Kaynağı bul ve analiz yap")
    assert result["status"] == "partial"
    assert "UNIT_MISMATCH" in {error["code"] for error in result["errors"]}
    assert "NO_READABLE_SOURCES" not in {error["code"] for error in result["errors"]}


def test_default_first_last_does_not_satisfy_declared_period_total(env):
    _, _, _, plan, build = env
    task = {"deliverables": ["analysis", "summary"], "summary": {"statistics": ["sum"]}}
    profit = {**plan,"columns":[{"name":"profit","metric_id":"profit","dimensions":{}}]}
    runtime, _ = build([call("execute", profit),call("plan_task", task),final()], max_decisions=3)
    result = runtime.run("Dönem toplamını hesapla")
    assert result["status"] == "partial"
    assert any(error.get("deliverable") == "summary" for error in result["errors"])
    assert not any(fact["statistic"] == "sum" for item in result["tool_results"]
                   if item["tool"] == "summarize_analysis" for fact in item["result"]["facts"])


def test_contract_cannot_be_weakened_after_research(env):
    *_, build = env
    runtime, _ = build([call("plan_task", {"deliverables": ["analysis", "chart"]}, "first"),
                        call("plan_task", {"deliverables": ["sources"]}, "weaken")], max_repairs=0)
    result = runtime.run("Analiz yap ve grafik çiz")
    assert result["status"] == "blocked"
    assert result["errors"][0]["code"] == "TASK_PLAN_LOCKED"


def test_task_plan_adds_source_known_summary_details_after_execution(env):
    _, _, journal, plan, build = env
    task = {"deliverables":["analysis","summary"]}
    refined = {**task,"summary":{"columns":["credit"],"statistics":["first","last"],
                                 "windows":[{"start":"2026-01","end":"2026-03"}]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "columns":["credit"],"statistics":["first","last"],"windows":[{"label":"Dönem","start":"2026-01","end":"2026-03"}]})
    runtime,_ = build([call("plan_task",task,"initial-plan"),call("execute",plan),
                       call("plan_task",refined,"source-known-plan"),summarize,final()])
    result = runtime.run("Kaynak dönemlerini karşılaştır")
    assert result["status"] == "completed"
    assert journal.get(result["run_id"])["state"]["task_plan"] == refined


def test_speculative_ratio_growth_plan_is_rejected_before_it_can_be_locked(env):
    _,_,journal,plan,build=env
    abstract={"deliverables":["analysis","summary"]}
    guessed={**abstract,"summary":{"columns":["credit","share"],"statistics":["first","last","growth"]}}
    actual={**plan,"columns":[*plan["columns"],{"name":"other_credit","metric_id":"credit","dimensions":{}}],
            "operations":[{"op":"ratio","column":"other_credit","denominator":"credit","output":"share","multiplier":100}]}
    valid={**abstract,"summary":{"columns":["credit","share"],"statistics":["first","last"]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "columns":["credit","share"],"statistics":["first","last"]})
    runtime,_=build([call("plan_task",guessed,"too-early"),call("plan_task",abstract,"abstract"),
                     call("execute",actual),call("plan_task",guessed,"incompatible"),
                     call("plan_task",valid,"schema-valid"),summarize,final()])
    result=runtime.run("Kaynak oranını ve ilk-son gözlemleri karşılaştır")
    assert result["status"]=="completed"
    rejected=[item["result"]["errors"][0]["code"] for item in result["tool_results"] if item["result"].get("status")=="blocked"]
    assert rejected==["TASK_PLAN_REQUIRES_ANALYSIS","TASK_PLAN_INVALID_SUMMARY"]
    assert journal.get(result["run_id"])["state"]["task_plan"]==valid


def test_empty_optional_summary_is_an_abstract_plan_without_a_repair(env):
    _,_,journal,plan,build=env
    task={"deliverables":["analysis"],"summary":{}}
    runtime,_=build([call("plan_task",task),call("execute",plan),final()])
    result=runtime.run("Kredi değerlerini göster")
    assert result["status"]=="completed" and result["repairs"]==0
    state=journal.get(result["run_id"])["state"]
    assert state["task_plan"]=={"deliverables":["analysis"]}
    first=next(message for message in state["messages"] if message.get("tool_calls"))
    assert json.loads(first["tool_calls"][0]["function"]["arguments"])["summary"]=={}


def test_summary_plan_windows_use_real_native_calendar_before_locking(env):
    _,_,journal,plan,build=env
    quarterly={**plan,"start":"2026-Q1","end":"2026-Q1","frequency":"quarterly",
               "columns":[{**plan["columns"][0],"alignment":"last"}]}
    task={"deliverables":["summary"],"summary":{"statistics":["last"],"windows":[{"start":"2026-03-31","end":"2026-03-31"}]}}
    valid={**task,"summary":{**task["summary"],"windows":[{"start":"2026-Q1","end":"2026-Q1"}]}}
    def summarize(messages):
        return call("summarize_analysis",{"analysis_id":result_of(messages,"execute")["analysis_id"],
            "statistics":["last"],"windows":[{"label":"Çeyrek","start":"2026-Q1","end":"2026-Q1"}]})
    runtime,_=build([call("execute",quarterly),call("plan_task",task,"wrong-calendar"),
                     call("plan_task",valid,"native-calendar"),summarize,final()])
    result=runtime.run("Çeyrek sonu bakiyesini göster")
    assert result["status"]=="completed"
    assert journal.get(result["run_id"])["state"]["task_plan"]==valid
    assert any(item["result"].get("errors",[{}])[0].get("code")=="TASK_PLAN_INVALID_SUMMARY" for item in result["tool_results"])


@pytest.mark.parametrize("change",["window","column","statistic","comparison"])
def test_task_refinement_cannot_replace_existing_summary_constraints(env,change):
    _,_,_,plan,build = env
    initial = {"deliverables":["summary"],"summary":{"columns":["credit"],"statistics":["first","last"],
        "windows":[{"start":"2026-01","end":"2026-02"},{"start":"2026-03","end":"2026-04"}],"compare_windows":True}}
    replacement = copy.deepcopy(initial)
    if change=="window": replacement["summary"]["windows"][0]["end"]="2026-03"
    elif change=="column": replacement["summary"]["columns"]=["profit"]
    elif change=="statistic": replacement["summary"]["statistics"]=["first"]
    else: replacement["summary"]["compare_windows"]=False
    runtime,_ = build([call("execute",{**plan,"end":"2026-04"}),call("plan_task",initial,"initial-plan"),call("plan_task",replacement,"replacement-plan")],max_repairs=0)
    result = runtime.run("Kaynak dönemlerini karşılaştır")
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "TASK_PLAN_LOCKED"


@pytest.mark.parametrize("mismatched_window", [False, True])
def test_exact_summary_windows_and_statistics_are_required(env, mismatched_window):
    _, _, _, plan, build = env
    task = {"deliverables": ["analysis", "summary"], "summary": {
        "columns": ["profit"], "statistics": ["sum"], "compare_windows": True,
        "windows": [{"start": "2026-01", "end": "2026-02"}, {"start": "2026-03", "end": "2026-04"}]}}
    profit = {**plan, "end": "2026-04", "columns": [{"name": "profit", "metric_id": "profit", "dimensions": {}}]}
    def summarize(messages):
        windows = [{"label": "Baz", **task["summary"]["windows"][0]}, {"label": "Sonraki", **task["summary"]["windows"][1]}]
        if mismatched_window:
            windows[0]["end"] = "2026-03"
            windows[1]["start"] = "2026-04"
        return call("summarize_analysis", {"analysis_id": result_of(messages, "execute")["analysis_id"],
            "statistics": ["sum"], "compare_windows": True,
            "windows": windows})
    runtime, _ = build([call("execute", profit),call("plan_task", task),summarize,final("Toplam 999, büyüme yüzde 888")], max_decisions=4)
    result = runtime.run("İki dönemin toplamını karşılaştır")
    if mismatched_window:
        assert result["status"] == "partial"
        assert any(error.get("deliverable") == "summary" for error in result["errors"])
        return
    assert result["status"] == "completed"
    assert "25" in result["message"] and "45" in result["message"] and "80" in result["message"]
    assert "999" not in result["message"] and "888" not in result["message"]


def test_missing_declared_output_gets_one_bounded_repair_opportunity(env):
    _, _, _, plan, build = env
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"})
    runtime, client = build([call("plan_task", {"deliverables": ["analysis", "chart"]}),
                             call("execute", plan), final("Grafik de hazır"), chart, final()])
    result = runtime.run("Kredi analizi ve grafik")
    assert result["status"] == "completed" and result["chart_updated"]
    assert len(client.requests) == 5
    assert any("Teslim kontrolü" in (message.get("content") or "") for message in client.requests[-1])


@pytest.mark.parametrize("repair_scaled_alias", [False, True])
@pytest.mark.parametrize("compiler_enabled", [False, True])
def test_real_parser_publication_analysis_summary_chart_chain_after_research(env, repair_scaled_alias, compiler_enabled):
    store, wid, _, _, build = env
    docs = DocumentTools(store, wid)
    url = "https://reports.example.org/new-company.csv"
    # Relevance must be established by the fetched source itself, not only
    # by the unverified search-result title.
    payload = b"month,New company monthly report value (million TL)\n2026-01,10\n2026-02,15\n2026-03,20\n"
    docs.web_search = lambda query, limit=5: {"status": "ok", "results": [{"title": "New company monthly report", "url": url, "snippet": "monthly report"}]}
    task = {"deliverables": ["sources", "dataset", "analysis", "summary", "chart"],
            "summary": {"columns": ["value"], "statistics": ["sum"], "windows": [{"start": "2026-01", "end": "2026-03"}]}}
    def inspect(messages):
        source = result_of(messages, "research_web")["sources"][0]
        return call("inspect_source", {"source_id": source["source_id"]})
    def publish(messages):
        source = result_of(messages, "inspect_source")
        return call("publish_selected_table", {"source_id": source["source_id"], "table_id": source["tables"][0]["table_id"],
            "expected_version": 0, "column_mapping": {"month": "month", "New_company_monthly_report_value_million_TL": "value"},
            "unit_evidence": {"value": "million TL"}, "contract": {"name": "new_company", "frequency": "monthly",
                "date_column": "month", "key": ["month"], "grain": ["month"], "expected_rows": 3,
                "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                            "value": {"dtype": "integer", "unit": "TRY_million", "kind": "flow", "nullable": False}}}})
    def ingest(messages):
        source = result_of(messages, "inspect_source")
        return call("ingest_source_table", {"source_id": source["source_id"], "table_id": source["tables"][0]["table_id"],
                                            "expected_version": 0, "row_numbers": [1, 2, 3]})
    def execute(messages):
        dataset = result_of(messages, "publish_selected_table")["dataset_id"]
        return call("execute", {"start": "2026-01", "end": "2026-03", "frequency": "monthly",
            "columns": [{"name": "value", "metric_id": f"overlay:{dataset}:value", "dimensions": {}}]})
    def summarize(messages):
        return call("summarize_analysis", {"analysis_id": result_of(messages, "execute")["analysis_id"], "statistics": ["sum"],
            "windows": [{"label": "İlk çeyrek", "start": "2026-01", "end": "2026-03"}]})
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "execute")["analysis_id"], "kind": "line"})
    def invalid_publish(messages):
        request = publish(messages)
        args = json.loads(request["tool_calls"][0]["function"]["arguments"])
        args["contract"]["columns"]["value"]["scale"] = 1000000
        return call("publish_selected_table", args, "bad-publish")
    publication_steps = [invalid_publish, final("Yayımlama başarısız, işlemi bitirdim."), publish] if repair_scaled_alias else [publish]
    responses=[call("plan_task",{"deliverables":task["deliverables"]},"abstract-plan"),
               call("research_web", {"query": "new company monthly report", "limit": 1}),inspect,
               *([ingest] if compiler_enabled else []), *publication_steps,
               execute,call("plan_task",task,"source-known-plan"),summarize,chart,final("999 milyon TL hesaplandı")]
    extras = {**docs.extra_tools(), **(FinancialImportTools(docs).extra_tools() if compiler_enabled else {})}
    runtime, client = build(responses,more=extras,max_decisions=len(responses))
    with patch("agentic_analytics.agent.tools.documents.fetch_public_url", return_value=(payload, "text/csv", url)):
        result = runtime.run("Yeni raporu bul, içeri al, ilk çeyrek toplamını hesapla ve çizgi grafik çiz")
    assert result["status"] == "completed", result
    assert result["analysis_updated"] and result["chart_updated"]
    assert "45 milyon TL" in result["message"] and "999" not in result["message"]
    frame, _ = store.load_analysis(result["analysis_id"])
    assert frame["value"].tolist() == [10, 15, 20]
    assert len(store.workspace(wid)["datasets"]) == 1
    assert len(client.requests) == (11 if repair_scaled_alias else 9) + int(compiler_enabled)
    assert "inspect_source" in {tool["function"]["name"] for tool in client.options[2]["tools"]}
    if compiler_enabled:
        assert "publish_selected_table" not in {tool["function"]["name"] for tool in client.options[0]["tools"]}
        assert "publish_selected_table" in {tool["function"]["name"] for tool in client.options[4]["tools"]}
        assert next(item["result"] for item in result["tool_results"] if item["tool"] == "ingest_source_table")["import_status"] == "unsupported_layout"


@pytest.mark.parametrize("error_code,retries", [("INVALID_UNPIVOT",1),("INVALID_SOURCE_DATE_FORMAT",1),("UNIT_MISMATCH",0)])
def test_final_repairs_only_explicit_representation_errors_once(env,error_code,retries):
    *_, build = env
    extra = {"prepare_source_table":{"schema":{"type":"function","function":{
        "name":"prepare_source_table","description":"Fixture preparation","parameters":obj({},[])}},
        "handler":lambda args:{"status":"blocked","errors":[{"code":error_code,"message":"Fixture source mapping error"}]}}}
    responses = [call("plan_task",{"deliverables":["dataset"]}),call("prepare_source_table",{}),final("İşlem tamamlanamadı.")]
    if retries:
        responses.append(final("Hâlâ tamamlanamadı."))
    runtime,client = build(responses,more=extra,max_decisions=5)
    result = runtime.run("Kaynak tabloyu hazırla")
    assert result["status"] == "blocked"
    assert len(client.requests) == 3+retries
    assert error_code in {error["code"] for error in result["errors"]}


def test_source_date_recovery_retains_exact_source_cells_in_model_view():
    recovery = {"header_rows":[{"row":10,"cells":[{"column":"column_9","text":"31 December 2025"}]}],
                "suggested_unpivot_update":{"period_format":"english_dmy","period_sources":{
                    "column_11":{"row":10,"columns":["column_9"],"date_index":0}}},
                "mapping_basis":"Actual source header cells; numeric cells unchanged."}
    raw = {"status":"blocked","errors":[{"code":"INVALID_SOURCE_DATE_FORMAT","message":"Select the actual source header date."}],
           "recovery":recovery}
    for tool in ("prepare_source_table","inspect_source"):
        assert _model_tool_result(tool,raw)["recovery"] == recovery


def test_inspected_calendar_retains_article_body_links_beyond_site_navigation():
    navigation = [{"title":f"Menu {index}","url":f"https://example.org/menu/{index}","in_main_content":False} for index in range(150)]
    decision = {"title":"6 Mart 2025","url":"https://example.org/releases/2025/decision-15","in_main_content":True}
    raw = {"status":"ok","source_id":"calendar","text":"Full site navigation "*500,
           "article":{"title":"Decisions 2025","readable_text":"6 Mart 2025 meeting decision",
                      "source_links":[*navigation,decision],"link_count":151}}
    view = _model_tool_result("inspect_source",raw)
    assert view["text"] == "6 Mart 2025 meeting decision"
    assert view["article"]["source_links"] == [{"title":decision["title"],"url":decision["url"]}]
    assert view["article"]["link_selection"] == "main_content"
    assert len(raw["article"]["source_links"]) == 151


def test_grouped_revision_recovers_and_charts_each_saved_group(env, tmp_path):
    store, wid, journal, _, build = env
    source = tmp_path / "groups.csv"
    source.write_text("month,bank,value\n2026-01,A,10\n2026-02,A,20\n2026-03,A,30\n2026-01,B,20\n2026-02,B,25\n2026-03,B,40\n")
    contract = {"name": "Grouped flows", "frequency": "monthly", "date_column": "month", "key": ["month", "bank"], "grain": ["month", "bank"],
        "columns": {"month": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    "bank": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False},
                    "value": {"dtype": "integer", "unit": "TRY", "scale": 1, "currency": "TRY", "kind": "flow", "nullable": False}}}
    dataset = store.ingest_csv(wid, source, contract, expected_version=0)["datasets"][-1]
    grouped = {"metric_id": f"overlay:{dataset}:value", "group_by": "bank", "dimensions": {},
               "start": "2026-01", "end": "2026-03", "frequency": "monthly", "limit": 10}
    def revise(messages):
        return call("revise_analysis", {"analysis_id": result_of(messages, "query_grouped")["analysis_id"],
            "operations": [{"op": "growth", "column": "value", "output": "change", "periods": 1}]}, "revision")

    class Crash(BaseException):
        pass
    original = journal.complete_step
    def interrupt(run_id, step_id, result):
        if step_id.endswith(":revision"):
            raise Crash()
        return original(run_id, step_id, result)
    runtime, _ = build([call("query_grouped", grouped), revise])
    with patch.object(journal, "complete_step", side_effect=interrupt), pytest.raises(Crash):
        runtime.run("Grupların değişimini hesapla ve çizgi grafik çiz", request_id="group-revision")
    before = store.workspace(wid)
    runtime, _ = build([call("create_chart", {"analysis_id": before["analysis_head"], "kind": "line", "columns": ["value"]}), final()])
    result = runtime.resume(journal.find_request(wid, "group-revision")["run_id"])
    assert result["status"] == "completed" and result["chart_updated"]
    assert store.workspace(wid)["version"] == before["version"]
    frame, manifest = store.load_analysis(result["analysis_id"])
    assert manifest["plan"]["query_type"] == "grouped"
    assert frame.loc[(frame["bank"] == "A") & (frame["period"] == "2026-02"), "change"].iloc[0] == 100
    assert frame.loc[(frame["bank"] == "B") & (frame["period"] == "2026-02"), "change"].iloc[0] == 25
    assert any(step["result"].get("recovered") for step in result["tool_results"] if step["tool"] == "revise_analysis")


def test_event_dataset_aggregation_is_delivered_as_analysis_and_chart(env, tmp_path):
    store, wid, _, _, build = env
    source = tmp_path / "events.csv"
    source.write_text("date,employee\n2026-01-03,A\n2026-01-15,B\n2026-02-02,C\n")
    contract = {"name": "Departures", "frequency": "event", "date_column": "date", "key": ["date", "employee"], "grain": ["date", "employee"],
        "columns": {"date": {"dtype": "date", "unit": "calendar", "kind": "dimension", "nullable": False},
                    "employee": {"dtype": "string", "unit": "label", "kind": "dimension", "nullable": False}}}
    dataset = store.ingest_csv(wid, source, contract, expected_version=0)["datasets"][-1]
    args = {"dataset_id": dataset, "measures": [{"name": "departures", "op": "count"}], "time_bucket": {"frequency": "monthly"}}
    def chart(messages):
        return call("create_chart", {"analysis_id": result_of(messages, "aggregate_dataset")["analysis_id"], "kind": "line"})
    runtime, _ = build([call("aggregate_dataset", args), chart, final("999 kişi ayrıldı.")], more=DatasetTools(store, wid).extra_tools())
    result = runtime.run("Aylık ayrılan çalışan sayısını tablo olarak göster ve grafik çiz")
    assert result["status"] == "completed" and result["analysis_updated"] and result["chart_updated"]
    frame, _ = store.load_analysis(result["analysis_id"])
    assert frame["departures"].tolist() == [2, 1]
    assert "999" not in result["message"]


def test_long_pdf_model_view_is_bounded_and_preserves_candidate_identity():
    source = {"status":"ok","source_id":"source_"+"a"*64,"total_pages":141,
              "text":"Financial report text "*10000,"pages":[{"page":i,"text":"Long page "*1000} for i in range(1,31)],
              "tables":[{"table_id":f"table_p{i:06d}_001","page":i,"columns":["period","value"],
                         "original_columns":{"period":"Period","value":"Amount (million TL)"},
                         "source_header_quotes":{"value":"Amount (million TL)"},"context_text":"Source context "*2000,
                         "row_count":100,"preview":[{"period":"2026-01","value":None},{"period":"2026-02","value":"150"}]}
                        for i in range(1,31)]}
    original = copy.deepcopy(source)
    view = _model_tool_result("inspect_source",source)
    assert len(json.dumps(view,ensure_ascii=False)) < 24000
    assert view["model_tables_truncated"] and view["full_evidence_retained"]
    assert view["tables"][0]["table_id"] == "table_p000001_001"
    assert view["tables"][0]["preview"][0]["value"] is None
    assert source == original


def test_source_row_reader_projection_preserves_stable_row_addresses():
    source = {"status":"ok","source_id":"source_"+"a"*64,"table_id":"table_001",
              "columns":["name","value"],"row_count":100,
              "rows":[{"candidate_row":i,"values":{"name":f"Source row {i}","value":str(i*10)}} for i in range(31,61)]}
    view = _model_tool_result("read_source_table",source)
    assert view["rows"][0] == {"candidate_row":31,"values":{"name":"Source row 31","value":"310"}}
    assert view["next_row_start"] == 51 and view["model_rows_truncated"]
