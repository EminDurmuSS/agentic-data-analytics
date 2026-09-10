"""Run real-data and CLI acceptance: python -m tools.validate_agent_lakehouse."""

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import duckdb

from agentic_analytics.lakehouse.service import LakehouseService
from agentic_analytics.lakehouse.store import LakehouseStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'tmp/agent-lakehouse-validation'
STORE = OUT / 'store'
checks = []

def check(name, passed, detail=None):
    if not passed:
        raise AssertionError(name)
    checks.append({'name': name, 'passed': True, 'detail': detail})

def cli(*args, expected=0):
    response = subprocess.run([sys.executable, '-m', 'agentic_analytics.lakehouse.cli', '--store', str(STORE), *args], capture_output=True, text=True, cwd=ROOT)
    if response.returncode != expected:
        raise AssertionError(response.stderr + response.stdout)
    return json.loads(response.stdout)

workspace = cli('init')
store = LakehouseStore(STORE)
service = LakehouseService(store, workspace['workspace_id'])
result = cli('--workspace', workspace['workspace_id'], 'demo')
(OUT / 'kobi-demo.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
check('cli_init_and_demo', result['status'] == 'ok')
kobi, kobi_manifest = store.load_analysis(result['revised']['analysis_id'])
check('kobi_66_periods', len(kobi) == 66)
check('first_year_yoy_explicitly_missing', kobi.loc[kobi.period.str.startswith('2021'), 'nominal_yoy_pct'].isna().all())
check('kobi_real_yoy', abs(result['explanation']['value'] - 12.863134535445763) < 1e-10, result['explanation']['value'])
check('reference_completeness_does_not_claim_file_verification', result['explanation']['source_references_complete'] and not result['explanation']['source_files_verified'])

housing_workspace = store.create_workspace(workspace['snapshot_id'])
housing = LakehouseService(store, housing_workspace['workspace_id'])
plan = {'start': '2021-01', 'end': '2025-12', 'frequency': 'monthly', 'columns': [
    {'name': 'credit', 'metric_id': 'bddk_monthly:table04:2:fffae80eca08:Toplam', 'dimensions': {'group_code': 10001}},
    {'name': 'rate', 'metric_id': 'evds:TP.KTF12', 'alignment': 'mean'}]}
first = housing.execute(plan)
f1, _ = store.load_analysis(first['analysis_id'])
second = housing.revise_analysis({'analysis_id': first['analysis_id'], 'add_columns': [{'name': 'cpi', 'metric_id': 'evds:TP.TUKFIY2025.GENEL'}], 'operations': [{'op': 'deflate', 'column': 'credit', 'index': 'cpi', 'base_period': '2021-01', 'output': 'credit'}]})
f2, _ = store.load_analysis(second['analysis_id'])
third = housing.revise_analysis({'analysis_id': second['analysis_id'], 'add_columns': [{'name': 'hpi', 'metric_id': 'evds:TP.KFE.TR'}]})
f3, _ = store.load_analysis(third['analysis_id'])
check('housing_60_months', len(f3) == 60)
check('only_credit_replaced', f1.rate.equals(f2.rate) and f1.period.equals(f2.period) and not f1.credit.equals(f2.credit))
check('hpi_addition_preserves_existing_cells', f2.equals(f3[f2.columns]) and f3.hpi.notna().all())
old, _ = store.load_analysis(first['analysis_id'])
check('old_result_remains_identical', old.equals(f1))
check('period_join_does_not_assert_population_equivalence', first['join_contract']['key'] == 'period' and not first['join_contract']['population_equivalence_asserted'])
with duckdb.connect(str(store.snapshot_path(workspace['snapshot_id'])), read_only=True) as connection:
    gold = connection.execute("SELECT housing_credit_real_million_tl_jan2021_prices AS value FROM analysis.housing_credit_monthly WHERE month BETWEEN '2021-01' AND '2025-12' ORDER BY month").fetchdf()
    residual = float((gold.value - f3.credit).abs().max())
    check('housing_matches_independent_gold', residual < 1e-7, residual)
    values = connection.execute("SELECT analysis_value FROM bddk.monthly_measurements WHERE metric_code='table06:1:38c9ed21984d:NakdiKrediToplam' AND group_code=10001 AND month IN ('2025-06','2026-06') ORDER BY month").fetchall()
    expected = (values[1][0] / values[0][0] - 1) * 100
    actual = float(kobi.loc[kobi.period == '2026-06', 'nominal_yoy_pct'].iloc[0])
    check('kobi_nominal_growth_independent_sql', abs(expected - actual) < 1e-10, actual)

proof = housing.explain_value({'analysis_id': third['analysis_id'], 'column': 'credit', 'period': '2025-12'})
check('housing_reference_lineage_complete', proof['source_references_complete'])
raw_checks = []
for leaf in proof['lineage']['inputs']:
    cell = leaf['source_cells'][0]
    path = ROOT / leaf['source_base'] / (cell.get('source_file') or cell.get('source_response_file'))
    payload = path.read_bytes()
    if leaf['hash_basis'] == 'decompressed_response' and path.suffix == '.gz':
        payload = gzip.decompress(payload)
    expected = cell.get('source_sha256') or cell.get('source_response_sha256')
    assert hashlib.sha256(payload).hexdigest() == expected
    data = json.loads(payload)
    if 'Json' in data:
        column = [item['name'] for item in data['Json']['colModels']].index(cell['value_dimension'])
        value = data['Json']['data']['rows'][cell['source_row_index'] - 1]['cell'][column]
    else:
        row = data['items'][cell['source_row_index'] - 1]
        value = row.get(cell['series_code'], row.get(cell['series_code'].replace('.', '_')))
    assert float(value) == cell['source_value']
    raw_checks.append({'metric_id': leaf['metric_id'], 'source_file': str(path.relative_to(ROOT)), 'source_hash_verified': True, 'source_cell_verified': True})
check('three_raw_hashes_and_cells_independently_verified', len(raw_checks) == 3)
(OUT / 'housing-proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2, allow_nan=False))

count_plan = {'start': '2025-06', 'end': '2026-06', 'frequency': 'monthly', 'columns': [{'name': 'count', 'metric_id': 'bddk_monthly:table06:5:414c01e26c04:NakdiKrediToplam', 'dimensions': {'group_code': 10001}}, {'name': 'cpi', 'metric_id': 'evds:TP.TUKFIY2025.GENEL'}], 'operations': [{'op': 'deflate', 'column': 'count', 'index': 'cpi', 'base_period': '2025-06', 'output': 'real'}]}
check('real_count_deflation_code', service.validate_plan(count_plan)['errors'][0]['code'] == 'UNIT_MISMATCH')
comparison = {'start': '2026-06', 'end': '2026-06', 'frequency': 'monthly', 'columns': [{'name': 'sector', 'metric_id': 'bddk_monthly:table06:1:38c9ed21984d:NakdiKrediToplam', 'dimensions': {'group_code': 10001}}, {'name': 'group', 'metric_id': 'bddk_monthly:table06:1:38c9ed21984d:NakdiKrediToplam', 'dimensions': {'group_code': 10002}}], 'operations': [{'op': 'ratio', 'column': 'group', 'denominator': 'sector', 'output': 'comparison'}]}
check('different_group_scope_blocked', service.validate_plan(comparison)['errors'][0]['code'] == 'SCOPE_MISMATCH')
comparison['operations'][0].update(scope_policy='explicit_comparison', scope_reason='Compare the reported deposit-bank amount with sector total, preserving the different scopes')
compared = service.execute(comparison)
check('intentional_scope_comparison_auditable', any(w['code'] == 'cross_scope_comparison' for w in compared['warnings']))

missing = service.discover({'query': '', 'status': 'metadata_only', 'limit': 1})['metrics'][0]['metric_id']
request = OUT / 'metadata-request.json'
request.write_text(json.dumps({'start': '2021-01', 'end': '2026-06', 'frequency': 'monthly', 'columns': [{'name': 'missing', 'metric_id': missing}]}))
blocked = cli('--workspace', workspace['workspace_id'], 'validate_plan', '--request', str(request), expected=2)
check('cli_structured_metadata_error', blocked['errors'][0]['code'] == 'METADATA_ONLY')
revision = OUT / 'stale-revision.json'
revision.write_text(json.dumps({'analysis_id': result['first']['analysis_id'], 'operations': [{'op': 'scale', 'column': 'sme_credit', 'output': 'credit_try', 'target_scale': 1}]}))
before = store.workspace(workspace['workspace_id'])['version']
conflict = cli('--workspace', workspace['workspace_id'], 'revise_analysis', '--request', str(revision), expected=2)
check('cli_version_conflict_does_not_change_workspace', conflict['errors'][0]['code'] == 'VERSION_CONFLICT' and before == store.workspace(workspace['workspace_id'])['version'])

with service._context() as (_, bindings, _):
    flow = next(b for b in bindings.values() if b.get('source_transformation') == 'difference_within_calendar_year' and b['status'] == 'ready')
flow_result = service.execute({'start': '2021-02', 'end': '2021-02', 'frequency': 'monthly', 'columns': [{'name': 'flow', 'metric_id': flow['metric_id'], 'dimensions': {'group_code': 10001}}]})
flow_proof = service.explain_value({'analysis_id': flow_result['analysis_id'], 'column': 'flow', 'period': '2021-02'})
cell = flow_proof['lineage']['source_cells'][0]
check('ytd_flow_includes_predecessor_raw_cell', bool(cell['previous_cumulative_source_cells']) and abs(cell['computed_value'] - cell['source_value'] + cell['previous_cumulative_source_cells'][0]['source_value']) < 1e-8)
report = {'status': 'passed', 'snapshot_id': workspace['snapshot_id'], 'checks': checks, 'independent_raw_source_checks': raw_checks, 'structured_error_examples': [blocked, conflict], 'housing_analyses': [first['analysis_id'], second['analysis_id'], third['analysis_id']], 'scope_comparison': compared}
(OUT / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
print(json.dumps({'status': 'passed', 'check_count': len(checks), 'snapshot_id': workspace['snapshot_id'], 'raw_sources_verified': len(raw_checks), 'nominal_yoy_pct': actual, 'real_yoy_pct': result['explanation']['value'], 'evidence': str(OUT / 'acceptance.json')}, ensure_ascii=False, indent=2))
