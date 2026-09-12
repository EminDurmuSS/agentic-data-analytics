import copy
import json
from pathlib import Path
import tempfile
import unittest

import duckdb
import pandas as pd

from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.service import PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class AnalysisSummaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with duckdb.connect(str(self.root / 'db.duckdb')) as db:
            db.execute('CREATE TABLE seed(value INTEGER)')
        self.store = LakehouseStore(self.root / 'store')
        self.snapshot = self.store.publish_snapshot(self.root / 'db.duckdb')
        self.store.create_workspace(self.snapshot['snapshot_id'], 'summary_test')
        self.tools = SummaryTools(self.store, 'summary_test')

    def save(self, values, periods=None, kind='flow', **extra):
        frame = pd.DataFrame({'period': periods or pd.period_range('2025-01', periods=len(values), freq='M').astype(str), 'profit': pd.Series(values, dtype='Float64'), **extra})
        schema = {'profit': {'kind': kind, 'unit': 'TRY', 'scale': 1000000, 'currency': 'TRY', 'status': 'ready'}}
        version = self.store.workspace('summary_test')['version']
        return self.store.save_analysis('summary_test', frame, {'frequency': 'monthly'}, {'frequency': 'monthly'},
                                        schema=schema, expected_version=version)['analysis_id']

    def test_explicit_windows_sum_and_comparison_are_exact_and_persisted(self):
        aid = self.save([47_347,70_867,98_437,47_551,62_632,95_625] + [1] * 6 + [87_249,82_152,119_287,74_625,58_476,106_642])
        before, _ = self.store.load_analysis(aid)
        result = self.tools.summarize_analysis(aid, statistics=['sum'], compare_windows=True,
            windows=[{'label':'2025 ilk altı ay','start':'2025-01','end':'2025-06'}, {'label':'2026 ilk altı ay','start':'2026-01','end':'2026-06'}])
        self.assertEqual([422459,528431,105972], [f['value'] for f in result['facts'][:3]])
        self.assertAlmostEqual(result['facts'][3]['value'], 25.084564419269096)
        self.assertEqual(result['facts'][3]['unit'], 'percent')
        self.assertEqual(len(result['facts'][3]['input_fact_ids']), 2)
        self.assertIn('25,084564', result['summary_text'])
        saved = self.tools.load_artifact(result['summary_id'])
        self.assertEqual(saved['facts'], result['facts'])
        after, _ = self.store.load_analysis(aid)
        pd.testing.assert_frame_equal(before, after)
        self.assertEqual(self.store.workspace('summary_test')['version'], 1)

    def test_stock_sum_is_refused_and_not_persisted(self):
        aid = self.save([100,200,300], kind='stock')
        with self.assertRaises(PlanError) as error:
            self.tools.summarize_analysis(aid, statistics=['sum'])
        self.assertEqual(error.exception.code, 'INVALID_TEMPORAL_AGGREGATION')
        self.assertEqual(list(self.tools.root.iterdir()), [])

    def test_null_or_missing_calendar_never_becomes_partial_sum(self):
        for values, periods in [([10,None,20],['2025-01','2025-02','2025-03']), ([10,20],['2025-01','2025-03']), ([None,None],['2025-01','2025-02'])]:
            with self.subTest(values=values):
                aid = self.save(values, periods)
                result = self.tools.summarize_analysis(aid, statistics=['sum','count'])
                self.assertIsNone(result['facts'][0]['value'])
                self.assertFalse(result['facts'][0]['complete'])
                self.assertTrue(result['warnings'])

    def test_window_outside_result_does_not_guess_missing_periods(self):
        aid = self.save([10,20])
        with self.assertRaises(PlanError) as error:
            self.tools.summarize_analysis(aid, windows=[{'label':'year','start':'2025-01','end':'2025-12'}])
        self.assertEqual(error.exception.code, 'SUMMARY_WINDOW_OUT_OF_RANGE')

    def test_rate_difference_has_percentage_point_unit(self):
        aid = self.save([45.,42.5], kind='rate')
        frame, manifest = self.store.load_analysis(aid)
        schema = copy.deepcopy(manifest['schema'])
        schema['profit'].update(unit='percent',scale=1,currency=None)
        aid = self.store.save_analysis('summary_test',frame,manifest['plan'],manifest['lineage'],schema=schema,expected_version=1)['analysis_id']
        result = self.tools.summarize_analysis(aid, statistics=['change'])
        self.assertEqual(result['facts'][0]['value'], -2.5)
        self.assertEqual(result['facts'][0]['unit'], 'percentage_point')
        with self.assertRaises(PlanError):
            self.tools.summarize_analysis(aid, statistics=['growth'])

    def test_summary_tampering_and_cross_workspace_read_are_rejected(self):
        aid = self.save([10,20])
        result = self.tools.summarize_analysis(aid)
        self.store.create_workspace(self.snapshot['snapshot_id'], 'other')
        with self.assertRaises(PlanError):
            SummaryTools(self.store, 'other').summarize_analysis(aid)
        path = self.tools.root / (result['summary_id'] + '.json')
        payload = json.loads(path.read_text())
        payload['facts'][0]['value'] = 999
        path.write_text(json.dumps(payload))
        with self.assertRaises(PlanError) as error:
            self.tools.load_artifact(result['summary_id'])
        self.assertEqual(error.exception.code, 'SUMMARY_INTEGRITY_ERROR')

    def test_window_comparison_cannot_bypass_unreviewed_semantics(self):
        original = self.save([10,20])
        frame, manifest = self.store.load_analysis(original)
        schema = copy.deepcopy(manifest['schema'])
        schema['profit']['status']='review_required'
        aid = self.store.save_analysis('summary_test',frame,manifest['plan'],manifest['lineage'],schema=schema,expected_version=1)['analysis_id']
        with self.assertRaises(PlanError) as error:
            self.tools.summarize_analysis(aid,statistics=['last'],compare_windows=True,
                windows=[{'label':'A','start':'2025-01','end':'2025-01'},{'label':'B','start':'2025-02','end':'2025-02'}])
        self.assertEqual(error.exception.code,'SEMANTICS_REVIEW_REQUIRED')

    def test_group_missing_boundary_does_not_substitute_another_period(self):
        frame = pd.DataFrame({'period':['2025-01','2025-02','2025-02'], 'bank':['A','A','B'], 'profit':[10,20,99]})
        schema = {'profit':{'kind':'flow','unit':'TRY','scale':1,'status':'ready'}, 'bank':{'kind':'dimension','unit':'label','status':'ready'}}
        aid = self.store.save_analysis('summary_test',frame,{'query_type':'grouped'}, {'frequency':'monthly','group_by':'bank'},schema=schema,expected_version=0)['analysis_id']
        result = self.tools.summarize_analysis(aid,statistics=['first','last'])
        first = next(f for f in result['facts'] if f['dimensions']=={'bank':'B'} and f['statistic']=='first')
        last = next(f for f in result['facts'] if f['dimensions']=={'bank':'B'} and f['statistic']=='last')
        self.assertIsNone(first['value'])
        self.assertEqual(last['value'],99)
