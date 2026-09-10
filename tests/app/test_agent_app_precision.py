"""Verify exact integers survive the actual HTTP table and evidence boundary."""
import pandas as pd
from fastapi.testclient import TestClient

from app.server import create_app
from agentic_analytics.lakehouse.service import LakehouseService


def test_unsafe_javascript_integer_remains_exact_in_table_evidence_and_export(tmp_path):
    app = create_app(runtime_root=tmp_path, source_db=None)
    with TestClient(app) as client:
        workspace = client.post('/api/workspaces', json={'name': 'Precision', 'profile': 'generic'}).json()
        workspace_id = workspace['workspace_id']
        source = tmp_path / 'counts.csv'
        expected = 9007199254740993
        pd.DataFrame({'date': ['2025-01'], 'count': [expected]}).to_csv(source, index=False)
        contract = {
            'name': 'Large counts',
            'columns': {'date': {'dtype': 'date', 'unit': 'date', 'kind': 'date', 'nullable': False},
                        'count': {'dtype': 'integer', 'unit': 'count', 'kind': 'count', 'nullable': False}},
            'key': ['date'], 'grain': ['date'], 'date_column': 'date', 'frequency': 'monthly',
        }
        store = app.state.context.store
        store.ingest_csv(workspace_id, source, contract, expected_version=0)
        service = LakehouseService(store, workspace_id)
        metric = service.discover({'query': 'count'})['metrics'][0]['metric_id']
        result = service.execute({'start': '2025-01', 'end': '2025-01', 'frequency': 'monthly',
                                  'columns': [{'name': 'count', 'metric_id': metric}]})
        base = f"/api/workspaces/{workspace_id}/analyses/{result['analysis_id']}"
        rows = client.get(base).json()['rows']
        assert rows[0]['count'] == {'$integer': str(expected)}
        proof = client.get(base + '/explain', params={'column': 'count', 'period': '2025-01'}).json()
        assert proof['value'] == {'$integer': str(expected)}
        assert str(expected) in client.get(base + '/csv').text
