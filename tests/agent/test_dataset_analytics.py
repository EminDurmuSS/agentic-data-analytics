from pathlib import Path
import tempfile
import unittest

import duckdb

from agentic_analytics.agent.tools.charts import ChartTools
from agentic_analytics.agent.tools.datasets import DatasetTools
from agentic_analytics.agent.tools.summary import SummaryTools
from agentic_analytics.lakehouse.service import LakehouseService, PlanError
from agentic_analytics.lakehouse.store import LakehouseStore


class DatasetAnalyticsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with duckdb.connect(str(self.root/'source.duckdb')) as db:
            db.execute('CREATE TABLE seed(value INTEGER)')
        self.store = LakehouseStore(self.root/'store')
        snapshot = self.store.publish_snapshot(self.root/'source.duckdb')
        self.store.create_workspace(snapshot['snapshot_id'],'test_dataset')
        self.tools = DatasetTools(self.store,'test_dataset')
        self.service = LakehouseService(self.store,'test_dataset')

    def ingest(self, content, contract):
        path = self.root/'source.csv'
        path.write_text(content)
        state = self.store.ingest_csv('test_dataset',path,contract,expected_version=self.store.workspace('test_dataset')['version'])
        return state['datasets'][-1]

    def contract(self, frequency='event', kind='flow'):
        return {'name':'test source','frequency':frequency,'date_column':'date','key':['date','id'],'grain':['date','id'],
                'columns':{'date':{'dtype':'date','unit':'calendar','kind':'dimension','nullable':False},
                           'id':{'dtype':'string','unit':'label','kind':'dimension','nullable':False},
                           'value':{'dtype':'integer','unit':'TRY','scale':1,'currency':'TRY','kind':kind,'nullable':True}}}

    def test_event_records_monthly_count_and_source_rows(self):
        dataset = self.ingest('date,id,value\n2026-01-03,A,10\n2026-01-03,B,20\n2026-02-08,A,30\n',self.contract())
        result = self.tools.aggregate_dataset(dataset,[{'name':'departures','op':'count'}],time_bucket={'frequency':'monthly'})
        frame,_ = self.store.load_analysis(result['analysis_id'])
        self.assertEqual(frame.departures.tolist(),[2,1])
        self.assertEqual(frame.period.tolist(),['2026-01','2026-02'])
        proof = self.service.explain_value({'analysis_id':result['analysis_id'],'period':'2026-01','column':'departures'})
        self.assertEqual(proof['lineage']['source_rows'],[1,2])
        self.assertTrue(proof['dataset_files_verified'])
        self.assertFalse(proof['source_files_verified'])

    def test_native_reported_facts_preserve_uncertainty_and_support_raw_chart(self):
        for kind, cumulative, review in [('unknown', False, False), ('flow', True, False), ('flow', False, True)]:
            with self.subTest(kind=kind, cumulative=cumulative, review=review):
                contract = self.contract(kind=kind)
                if cumulative:
                    contract['columns']['value']['temporal_semantics'] = 'year to date'
                if review:
                    contract['columns']['value'].update(status='review_required', temporal_semantics='reported_interval_unresolved')
                dataset = self.ingest('date,id,value\n2026-06-30,A,34333\n2025-06-30,A,24850\n', contract)
                result = self.tools.aggregate_dataset(dataset,
                    [{'name': 'reported', 'op': 'source_value', 'column': 'value'}],
                    group_by='id', time_bucket={'frequency': 'daily'})
                frame, manifest = self.store.load_analysis(result['analysis_id'])
                self.assertEqual(frame.period.tolist(), ['2025-06-30', '2026-06-30'])
                self.assertEqual(frame.reported.tolist(), [24850, 34333])
                self.assertEqual(manifest['schema']['reported']['status'], 'review_required')
                self.assertFalse(manifest['schema']['reported']['additive_over_time'])
                chart = ChartTools(self.store, 'test_dataset').create_chart({
                    'analysis_id': result['analysis_id'], 'kind': 'bar', 'columns': ['reported']})
                self.assertEqual(chart['status'], 'ok')
                proof = self.service.explain_value({'analysis_id': result['analysis_id'],
                    'period': '2026-06-30', 'column': 'reported', 'dimensions': {'id': 'A'}})
                self.assertEqual(proof['value'], 34333)
                self.assertEqual(proof['lineage']['source_values'], [34333])
                with self.assertRaises(PlanError):
                    SummaryTools(self.store, 'test_dataset').summarize_analysis(
                        result['analysis_id'], columns=['reported'], statistics=['sum'])
                with self.assertRaises(PlanError):
                    self.tools.aggregate_dataset(dataset, [{'name': 'calculated', 'op': 'mean', 'column': 'value'}],
                        group_by='id', time_bucket={'frequency': 'daily'})

    def test_reported_facts_cannot_collapse_periods_or_population_records(self):
        dataset = self.ingest('date,id,value\n2026-01-03,A,10\n2026-01-03,B,20\n2026-01-04,A,30\n', self.contract(kind='unknown'))
        measures = [{'name': 'reported', 'op': 'source_value', 'column': 'value'}]
        for options in [
            {'group_by': 'id', 'time_bucket': {'frequency': 'monthly'}},
            {'time_bucket': {'frequency': 'daily'}},
            {'time_bucket': {'frequency': 'daily'}, 'aggregation_scope': 'explicit_population',
             'scope_reason': 'Attempt to silently select one of two distinct source entities.'},
        ]:
            with self.subTest(options=options), self.assertRaises(PlanError):
                self.tools.aggregate_dataset(dataset, measures, **options)

    def test_reported_facts_preserve_null_and_exact_large_integer(self):
        dataset = self.ingest('date,id,value\n2026-01-03,A,9007199254740993\n2026-01-04,A,\n', self.contract(kind='unknown'))
        args = {'dataset_id': dataset, 'measures': [{'name': 'reported', 'op': 'source_value', 'column': 'value'}],
                'group_by': 'id', 'time_bucket': {'frequency': 'daily'}}
        before = self.store.workspace('test_dataset')
        result = self.tools.aggregate_dataset(**args)
        self.assertEqual(result['preview'][0]['reported'], 9007199254740993)
        self.assertIsNone(result['preview'][1]['reported'])
        recovered = self.tools.recover(args, {'input_revision': before['revision_id']})
        self.assertEqual(result['analysis_id'], recovered['analysis_id'])

    def test_static_without_date_does_not_break_discovery_and_supports_chart(self):
        contract = {'name':'Departments','frequency':'static','key':['department'],'grain':['department'],
                    'columns':{'department':{'dtype':'string','unit':'label','kind':'dimension','nullable':False},
                               'employees':{'dtype':'integer','unit':'count','kind':'count_stock','nullable':False}}}
        dataset = self.ingest('department,employees\nEngineering,12\nFinance,7\n',contract)
        self.assertEqual(self.service.discover({'query':'employees'})['status'],'ok')
        result = self.tools.aggregate_dataset(dataset,[{'name':'staff','column':'employees','op':'sum'}],group_by='department')
        frame,_ = self.store.load_analysis(result['analysis_id'])
        self.assertEqual(sorted(frame.staff.tolist()),[7,12])
        chart = ChartTools(self.store,'test_dataset').create_chart({'analysis_id':result['analysis_id'],'kind':'bar','columns':['staff']})
        self.assertEqual(chart['status'],'ok')
        proof = self.service.explain_value({'analysis_id':result['analysis_id'],'period':'Tüm kayıtlar','column':'staff','dimensions':{'department':'Finance'}})
        self.assertEqual(proof['value'],7)

    def test_nulls_cannot_disappear_from_population_sum(self):
        dataset = self.ingest('date,id,value\n2026-01-03,A,10\n2026-01-04,B,\n',self.contract())
        args = {'dataset_id':dataset,'measures':[{'name':'amount','op':'sum','column':'value'}],'time_bucket':{'frequency':'monthly'}}
        with self.assertRaises(PlanError) as error:
            self.tools.aggregate_dataset(**args)
        self.assertEqual(error.exception.code,'SCOPE_MISMATCH')
        result = self.tools.aggregate_dataset(**args,aggregation_scope='explicit_population',scope_reason='Distinct invoice records, one row per invoice and event date.')
        self.assertIsNone(result['preview'][0]['amount'])
        self.assertTrue(any(w['code']=='MISSING_AGGREGATE_INPUT' for w in result['warnings']))

    def test_stocks_from_different_dates_cannot_be_summed_even_explicitly(self):
        dataset = self.ingest('date,id,value\n2026-01-03,A,10\n2026-01-04,A,20\n',self.contract(kind='stock'))
        with self.assertRaises(PlanError) as error:
            self.tools.aggregate_dataset(dataset,[{'name':'amount','op':'sum','column':'value'}],time_bucket={'frequency':'monthly'},aggregation_scope='explicit_population',scope_reason='An explicit but invalid attempt to add stock observations.')
        self.assertEqual(error.exception.code,'INVALID_TEMPORAL_AGGREGATION')

    def test_missing_month_is_not_a_complete_quarter_sum(self):
        dataset = self.ingest('date,id,value\n2026-01,A,10\n2026-03,A,20\n',self.contract(frequency='monthly'))
        result = self.tools.aggregate_dataset(dataset,[{'name':'amount','op':'sum','column':'value'}],time_bucket={'frequency':'quarterly'},filters=[{'column':'id','op':'eq','value':'A'}])
        self.assertIsNone(result['preview'][0]['amount'])

    def test_weighted_mean_filters_and_recovery_preserve_inputs(self):
        contract = self.contract()
        contract['columns']['weight']={'dtype':'integer','unit':'count','kind':'count_stock','nullable':False}
        dataset = self.ingest('date,id,value,weight\n2026-01-03,A,10,1\n2026-01-04,B,20,3\n2026-02-01,C,999,1\n',contract)
        args = {'dataset_id':dataset,'measures':[{'name':'amount','op':'weighted_mean','column':'value','weight':'weight'}],
                'time_bucket':{'frequency':'monthly'},'aggregation_scope':'explicit_population','scope_reason':'Independent observations, weighted by the explicit count column.',
                'filters':[{'column':'date','op':'lte','value':'2026-01-31'}]}
        before = self.store.workspace('test_dataset')
        result = self.tools.aggregate_dataset(**args)
        self.assertEqual(result['preview'][0]['amount'],17.5)
        recovered = self.tools.recover(args,{'input_revision':before['revision_id'],'workspace_version':before['version']})
        self.assertEqual(recovered['analysis_id'],result['analysis_id'])
        self.assertEqual(self.store.workspace('test_dataset')['version'],before['version']+1)

    def test_unknown_dataset_and_empty_filter_do_not_create_zero(self):
        with self.assertRaises(PlanError):
            self.tools.aggregate_dataset('not-in-workspace',[{'name':'count','op':'count'}])
        dataset = self.ingest('date,id,value\n2026-01-03,A,10\n',self.contract())
        with self.assertRaises(PlanError) as error:
            self.tools.aggregate_dataset(dataset,[{'name':'count','op':'count'}],filters=[{'column':'id','op':'eq','value':'absent'}],time_bucket={'frequency':'monthly'})
        self.assertEqual(error.exception.code,'MISSING_OBSERVATIONS')

    def test_nullable_large_integer_output_preserves_exact_source_amount(self):
        dataset = self.ingest('date,id,value\n2026-01-01,A,9007199254740993\n2026-02-01,A,\n', self.contract())
        result = self.tools.aggregate_dataset(dataset, [{'name':'amount','op':'sum','column':'value'}],
            filters=[{'column':'id','op':'eq','value':'A'}], time_bucket={'frequency':'monthly'})
        frame, _ = self.store.load_analysis(result['analysis_id'])
        self.assertEqual(int(frame.amount.iloc[0]), 9007199254740993)
        self.assertTrue(frame.amount.isna().iloc[1])
        self.assertEqual(result['preview'][0]['amount'], 9007199254740993)

    def test_calendar_completeness_is_checked_for_every_entity(self):
        dataset = self.ingest('date,id,value\n2026-01,A,10\n2026-03,A,30\n2026-02,B,20\n', self.contract(frequency='monthly'))
        result = self.tools.aggregate_dataset(dataset,[{'name':'amount','op':'sum','column':'value'}],time_bucket={'frequency':'quarterly'},
            aggregation_scope='explicit_population',scope_reason='Two separate branches whose monthly flows are to be combined.')
        self.assertIsNone(result['preview'][0]['amount'])
        self.assertTrue(any(w['code']=='MISSING_AGGREGATE_INPUT' for w in result['warnings']))

    def test_distinct_people_and_flow_means_do_not_become_additive_flows(self):
        dataset = self.ingest('date,id,value\n2026-01-01,A,10\n2026-01-02,B,20\n2026-02-01,A,30\n', self.contract())
        for measure in [{'name':'people','op':'count_distinct','column':'id'}, {'name':'average','op':'mean','column':'value'}]:
            with self.subTest(operation=measure['op']):
                result = self.tools.aggregate_dataset(dataset,[measure],time_bucket={'frequency':'monthly'},
                    aggregation_scope='explicit_population',scope_reason='Independent event observations selected for a monthly descriptive summary.')
                with self.assertRaises(PlanError) as error:
                    SummaryTools(self.store,'test_dataset').summarize_analysis(result['analysis_id'],statistics=['sum'])
                self.assertEqual(error.exception.code,'INVALID_TEMPORAL_AGGREGATION')

    def test_period_source_column_is_not_overwritten_by_the_bucket(self):
        contract = self.contract(kind='stock')
        contract['columns']['period'] = contract['columns'].pop('date')
        contract.update(date_column='period',key=['period','id'],grain=['period','id'])
        dataset = self.ingest('period,id,value\n2026-01-03,A,10\n2026-01-04,A,20\n',contract)
        with self.assertRaises(PlanError) as error:
            self.tools.aggregate_dataset(dataset,[{'name':'amount','op':'sum','column':'value'}],time_bucket={'frequency':'monthly'},
                aggregation_scope='explicit_population',scope_reason='An invalid attempt to sum snapshots taken on different dates.')
        self.assertEqual(error.exception.code,'INVALID_TEMPORAL_AGGREGATION')
        distinct = self.tools.aggregate_dataset(dataset,[{'name':'dates','op':'count_distinct','column':'period'}],time_bucket={'frequency':'monthly'})
        self.assertEqual(distinct['preview'][0]['dates'],2)

    def test_weight_total_does_not_overflow_int64(self):
        contract = self.contract()
        contract['columns']['weight']={'dtype':'integer','unit':'count','kind':'count_stock','nullable':False}
        dataset = self.ingest('date,id,value,weight\n2026-01-03,A,10,9000000000000000000\n2026-01-04,B,20,9000000000000000000\n',contract)
        result = self.tools.aggregate_dataset(dataset,[{'name':'amount','op':'weighted_mean','column':'value','weight':'weight'}],time_bucket={'frequency':'monthly'},
            aggregation_scope='explicit_population',scope_reason='Independent observations with explicitly supplied positive weights.')
        self.assertEqual(result['preview'][0]['amount'],15)
