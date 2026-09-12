"""Independent acceptance checks reject false completion and corrupted receipts."""
import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from evals.mentor_grading import ORACLES, ReceiptReader, canonical, chart_checks, date_in_text, digest, grade_round, grade_turn, period_end, pdf_sector_scope_disclosures, read_json


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(canonical(value))


class SavedTrial:
    def __init__(self,path):
        self.round=path
        self.root=path/'runtime/lakehouse'
        self.workspace='workspace_fixture'

    def object(self,kind,rows,meta):
        prefix={'analyses':'analysis','datasets':'dataset'}[kind]
        data=self.round/'staged.parquet'
        pd.DataFrame(rows).to_parquet(data,index=False)
        manifest={**meta,'row_count':len(rows),'data_sha256':digest(data)}
        identifier=prefix+'_'+hashlib.sha256(canonical(manifest)).hexdigest()
        destination=self.root/kind/identifier
        destination.mkdir(parents=True)
        data.replace(destination/'data.parquet')
        manifest={**manifest,prefix+'_id':identifier}
        write_json(destination/'manifest.json',manifest)
        return manifest

    def source(self,url,body,mime='text/html'):
        raw=body.encode()
        sha=hashlib.sha256(raw).hexdigest()
        identity={'filename':'fixture','source_url':url,'raw_sha256':sha,'mime_type':mime,
                  'size_bytes':len(raw),'workspace_id':self.workspace}
        sid='source_'+hashlib.sha256(canonical(identity)).hexdigest()
        manifest={**identity,'source_id':sid}
        folder=self.root/'document_sources'/self.workspace/sid
        write_json(folder/'manifest.json',manifest)
        (folder/'raw.bin').write_bytes(raw)
        return manifest

    def dataset(self,rows,source,page=None):
        proof={'source_id':source['source_id'],'raw_sha256':source['raw_sha256'],'source_url':source['source_url'],'page':page}
        return self.object('datasets',rows,{'contract':{'document_provenance':proof}})

    def pdf_dataset(self,rows,source,oracle,source_column=None):
        origins=[]
        for row in rows:
            role=row['metric']
            at=period_end(row['period'])
            column=source_column or next(name for name,spec in oracle['source_cells'].items()
                                        if spec['date']==at and role in spec['rows'].values())
            source_number=next((int(number) for spec in oracle['source_cells'].values()
                                for number,meaning in spec['rows'].items() if meaning==role))
            origins.append({'value':{'candidate_column':column,'candidate_row':source_number}})
        proof={'source_id':source['source_id'],'raw_sha256':source['raw_sha256'],'source_url':source['source_url'],
               'page':oracle['page'],'preparation':{'source_table_id':oracle['source_table']},'cell_origins':origins}
        dataset=self.object('datasets',rows,{'contract':{'document_provenance':proof}})
        cells={json.dumps([row['period'],row['metric']],ensure_ascii=False):{'value':{'source_column':'value',
               'source_rows':[index],'source_values':[row['value']]}} for index,row in enumerate(rows,1)}
        lineage={'group_by':'metric','dataset_query':{'dataset_id':dataset['dataset_id'],'document_provenance':proof,
                 'output_keys':['period','metric'],'cells':cells}}
        return dataset,lineage

    def analysis(self,rows,schema,*,lineage=None,datasets=()):
        manifest=self.object('analyses',rows,{'workspace_id':self.workspace,'schema':schema,'lineage':lineage or {},'datasets':list(datasets)})
        return {'result':{'status':'completed','analysis_id':manifest['analysis_id'],'workspace_id':self.workspace,'tool_results':[],
                          'message':'Kurgusal örnek kaydedildi.'},
                'analyses':{manifest['analysis_id']:{'manifest':manifest,'rows':copy.deepcopy(rows)}}}

    def receipt(self,turn,kind,payload):
        manifest=next(iter(turn['analyses'].values()))['manifest']
        prefix={'charts':'chart','summaries':'summary'}[kind]
        payload={**payload,'workspace_id':self.workspace,'analysis_id':manifest['analysis_id'],'provenance':{'data_sha256':manifest['data_sha256']}}
        encoded=canonical(payload)
        identifier=prefix+'_'+hashlib.sha256(encoded).hexdigest()
        path=self.root/kind/self.workspace/(identifier+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(encoded)
        if kind=='charts':
            turn['result']['chart_id']=identifier
        else:
            turn['result']['tool_results'].append({'tool':'summarize_analysis','result':{'status':'ok','artifact_id':identifier,'analysis_id':manifest['analysis_id']}})
        return path

    def chart(self,turn,kind,columns,group_by=None):
        rows=next(iter(turn['analyses'].values()))['rows']
        periods=sorted({row['period'] for row in rows})
        groups=sorted({row[group_by] for row in rows}) if group_by else [None]
        series=[]
        for group in groups:
            chosen={row['period']:row for row in rows if group_by is None or row[group_by]==group}
            for name in columns:
                meta=next(iter(turn['analyses'].values()))['manifest']['schema'][name]
                unit='%' if meta.get('unit')=='percent' else {1:'TL',1000:'bin TL',1000000:'milyon TL'}.get(meta.get('scale'))
                series.append({'column':name,'dimensions':{group_by:group} if group_by else {},
                               'raw_values':[chosen.get(at,{}).get(name) for at in periods],
                               'values':[chosen.get(at,{}).get(name) for at in periods],'unit':unit,'raw_unit':unit})
        return self.receipt(turn,'charts',{'spec':{'kind':kind,'normalize':'none'},'series':series,'periods':periods,'complete':True})


def money(kind='stock',scale=1000000,metric='bddk_monthly:fixture'):
    return {'unit':'TRY','currency':'TRY','scale':scale,'kind':kind,'metric_id':metric}


def profit_trial(path,*,wrong_monthly=False,wrong_growth=False):
    saved=SavedTrial(path)
    values={2025:[47347,70867,98437,47551,62632,95625],2026:[87249,82152,119287,74625,58476,106642]}
    rows=[{'period':f'{year}-{month:02d}','profit':value} for year,items in values.items() for month,value in enumerate(items,1)]
    if wrong_monthly: rows[1]['profit']+=rows[0]['profit']
    turn=saved.analysis(rows,{'profit':money('flow')})
    facts=[{'column':'profit','statistic':'sum','period_start':year+'-01','period_end':year+'-06','value':total,'unit':'TRY','scale':1000000}
           for year,total in ORACLES['profit']['totals'].items()]
    facts.append({'column':'profit','statistic':'window_growth','basis_statistic':'sum','period_start':'2025-01','period_end':'2026-06',
                  'unit':'percent','value':999 if wrong_growth else 25.08456441926909})
    saved.receipt(turn,'summaries',{'facts':facts})
    return saved,turn


def new_source_trial(path,*,wrong_sector=False):
    saved=SavedTrial(path)
    oracle=ORACLES['new_source']
    sector=[23640215,24211722,24901164] if wrong_sector else oracle['sector']
    rows=[{'period':at,'bank':bank,'sector':total,'share':bank/total*100}
          for at,bank,total in zip(oracle['periods'],oracle['bank'],sector)]
    source=saved.source('https://example.org/upload.csv','month,amount\n2026-01,100000\n','text/csv')
    dataset=saved.dataset(rows,source)
    schema={'bank':money(metric='overlay:'+dataset['dataset_id']+':bank'),'sector':money(),'share':{'unit':'percent','kind':'ratio','scale':1}}
    lineage={'operations':[{'op':'ratio','column':'bank','denominator':'sector','output':'share','multiplier':100,'scope_policy':'explicit_comparison'}]}
    turn=saved.analysis(rows,schema,lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'line',['share'])
    return saved,turn


def test_completed_status_without_saved_analysis_is_incomplete(tmp_path):
    result=grade_turn(tmp_path,'profit',{'result':{'status':'completed','message':'422459 and 528431, all done'}})
    assert result['artifact_grade']=='incomplete' and not result['task_complete']


@pytest.mark.parametrize('wrong_monthly,wrong_growth',[(False,False),(True,False),(False,True)])
def test_profit_checks_saved_monthly_values_and_actual_summary_receipt(tmp_path,wrong_monthly,wrong_growth):
    _,turn=profit_trial(tmp_path,wrong_monthly=wrong_monthly,wrong_growth=wrong_growth)
    grade=grade_turn(tmp_path,'profit',turn)
    assert grade['artifact_grade']==('fail' if wrong_monthly or wrong_growth else 'pass')
    assert grade['task_complete'] is not (wrong_monthly or wrong_growth)


def test_successful_numeric_evidence_does_not_hide_partial_delivery(tmp_path):
    _,turn=profit_trial(tmp_path)
    turn['result']['status']='partial'
    grade=grade_turn(tmp_path,'profit',turn)
    assert grade['artifact_grade']=='pass' and not grade['task_complete']


@pytest.mark.parametrize('wrong_sector',[False,True])
def test_same_looking_restricted_sector_table_cannot_pass_full_sector_oracle(tmp_path,wrong_sector):
    _,turn=new_source_trial(tmp_path,wrong_sector=wrong_sector)
    grade=grade_turn(tmp_path,'new_source',turn)
    assert grade['artifact_grade']==('fail' if wrong_sector else 'pass')
    assert grade['task_complete'] is not wrong_sector


def test_export_copy_cannot_override_saved_parquet(tmp_path):
    _,turn=profit_trial(tmp_path)
    next(iter(turn['analyses'].values()))['rows'][0]['profit']=999
    grade=grade_turn(tmp_path,'profit',turn)
    assert grade['artifact_grade']=='fail'
    assert next(c for c in grade['checks'] if c['check']=='collector_export_matches_saved_bytes')['status']=='fail'


def test_parquet_and_summary_corruption_are_rejected(tmp_path):
    saved,turn=profit_trial(tmp_path)
    summary=next((saved.root/'summaries').rglob('*.json'))
    summary.write_bytes(summary.read_bytes()+b' ')
    grade=grade_turn(tmp_path,'profit',turn)
    assert grade['artifact_grade']=='fail'
    assert next(c for c in grade['checks'] if c['check']=='summary_receipt_integrity')['status']=='fail'
    data=next((saved.root/'analyses').rglob('data.parquet'))
    data.write_bytes(data.read_bytes()+b'corrupted')
    assert grade_turn(tmp_path,'profit',turn)['artifact_grade']=='fail'


def test_hash_valid_chart_must_still_match_saved_cells(tmp_path):
    saved,turn=new_source_trial(tmp_path)
    saved.receipt(turn,'charts',{'spec':{'kind':'line','normalize':'none'},'series':[{'column':'share','raw_values':[999,999,999]}],
                                'periods':ORACLES['new_source']['periods'],'complete':True})
    grade=grade_turn(tmp_path,'new_source',turn)
    assert grade['artifact_grade']=='fail'
    assert next(c for c in grade['checks'] if c['check']=='chart_series_0_matches_saved_cells')['status']=='fail'


@pytest.mark.parametrize('calendar_page,reversed_rates',[(False,False),(True,False),(False,True)])
def test_web_requires_real_decision_receipt_and_correct_rate_direction(tmp_path,calendar_page,reversed_rates):
    saved=SavedTrial(tmp_path)
    url='https://www.tcmb.gov.tr/PPK/2025' if calendar_page else 'https://www.tcmb.gov.tr/duyurular/basin/2025/duy2025-15'
    rates='yüzde 42,5’ten yüzde 45’e' if reversed_rates else 'yüzde 45’ten yüzde 42,5’e'
    source=saved.source(url,'<main><p>6 Mart 2025. Bir hafta vadeli repo ihale faiz oranı '+rates+' indirilmiştir.</p></main>')
    turn={'result':{'status':'completed','workspace_id':saved.workspace,'message':'6 Mart 2025: Bir hafta repo faizi '+rates+'. Kaynak: '+url,
                   'tool_results':[{'tool':'inspect_source','result':{'status':'ok',**source}}]}}
    grade=grade_turn(tmp_path,'web',turn)
    assert grade['artifact_grade']==('incomplete' if calendar_page else 'fail' if reversed_rates else 'pass')


@pytest.mark.parametrize('quarterly,wrong_scale,swap_labels',[(False,False,False),(True,False,False),(False,True,False),(False,False,True)])
def test_pdf_checks_total_cells_with_periods_units_and_source_page(tmp_path,monkeypatch,quarterly,wrong_scale,swap_labels):
    saved=SavedTrial(tmp_path)
    rows=[{'period':{'2025-12-31':'2025-Q4','2026-03-31':'2026-Q1'}[at] if quarterly else at,
           'metric':{'financial_assets':'cash','cash':'financial_assets'}[role] if swap_labels else role,'value':value}
          for role,values in ORACLES['garanti_pdf']['values'].items() for at,value in values.items()]
    source=saved.source('https://www.garantibbvainvestorrelations.com/report.pdf','synthetic PDF fixture','application/pdf')
    monkeypatch.setitem(ORACLES,'garanti_pdf',{**ORACLES['garanti_pdf'],'raw_sha256':source['raw_sha256']})
    dataset,lineage=saved.pdf_dataset(rows,source,ORACLES['garanti_pdf'])
    turn=saved.analysis(rows,{'value':money(scale=1 if wrong_scale else 1000)},lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    grade=grade_turn(tmp_path,'garanti_pdf',turn)
    assert grade['artifact_grade']==('fail' if wrong_scale or swap_labels else 'pass')


@pytest.mark.parametrize('price_basis',['30 June 2025 purchasing power','2025-12-31 purchasing power',None])
def test_tupras_purchasing_power_is_part_of_numeric_acceptance(tmp_path,monkeypatch,price_basis):
    saved=SavedTrial(tmp_path)
    rows=[{'period':at,'metric':role,'value':value} for role,values in ORACLES['tupras_pdf']['values'].items() for at,value in values.items()]
    source=saved.source('https://www.tupras.com.tr/report.pdf','synthetic nonbank PDF fixture','application/pdf')
    monkeypatch.setitem(ORACLES,'tupras_pdf',{**ORACLES['tupras_pdf'],'raw_sha256':source['raw_sha256']})
    dataset,lineage=saved.pdf_dataset(rows,source,ORACLES['tupras_pdf'])
    turn=saved.analysis(rows,{'value':{**money(scale=1000),'price_basis':price_basis}},lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    grade=grade_turn(tmp_path,'tupras_pdf',turn)
    assert grade['artifact_grade']==('pass' if price_basis=='30 June 2025 purchasing power' else 'fail')


@pytest.mark.parametrize('source_column',['column_7','column_10'])
def test_static_snapshot_date_requires_exact_source_coordinates(tmp_path,monkeypatch,source_column):
    saved=SavedTrial(tmp_path)
    rows=[{'period':'Tüm kayıtlar','metric':'cash','value':90351730},
          {'period':'Tüm kayıtlar','metric':'total_assets','value':545630257}]
    source=saved.source('https://www.tupras.com.tr/report.pdf','synthetic dated snapshot','application/pdf')
    monkeypatch.setitem(ORACLES,'tupras_pdf',{**ORACLES['tupras_pdf'],'raw_sha256':source['raw_sha256']})
    dataset,lineage=saved.pdf_dataset(rows,source,ORACLES['tupras_pdf'],source_column=source_column)
    turn=saved.analysis(rows,{'value':{**money(scale=1000),'price_basis':'2025-06-30 purchasing power'}},lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    grade=grade_turn(tmp_path,'tupras_pdf',turn)
    assert grade['artifact_grade']==('pass' if source_column=='column_7' else 'incomplete')


def test_date_basis_accepts_explicit_english_or_turkish_source_dates():
    for text in ('2025-06-30 purchasing power','30 June 2025 purchasing power','30 Haziran 2025 satın alma gücü'):
        assert date_in_text(text,'2025-06-30')
    assert not date_in_text('2025-12-31 purchasing power','2025-06-30')


def test_profit_correct_half_year_sums_cannot_hide_wrong_individual_months(tmp_path):
    saved,original=profit_trial(tmp_path)
    rows=[{'period':f'{year}-{month:02d}','profit':1 if month<6 else total-5}
          for year,total in ORACLES['profit']['totals'].items() for month in range(1,7)]
    turn=saved.analysis(rows,{'profit':money('flow')})
    old=original['result']['tool_results'][0]['result']['artifact_id']
    summary=json.loads((saved.root/'summaries'/saved.workspace/(old+'.json')).read_text())
    saved.receipt(turn,'summaries',{'facts':summary['facts']})
    grade=grade_turn(tmp_path,'profit',turn)
    assert grade['artifact_grade']=='fail'
    assert next(c for c in grade['checks'] if c['check']=='twelve_independent_monthly_profit_cells')['status']=='fail'


@pytest.mark.parametrize('defect',['wrong_display','missing_display','one_period','extra_period','normalized','duplicate_series','wrong_unit'])
def test_hash_valid_chart_cannot_hide_display_or_coverage_defects(tmp_path,defect):
    saved,turn=new_source_trial(tmp_path)
    path=saved.root/'charts'/saved.workspace/(turn['result']['chart_id']+'.json')
    chart=json.loads(path.read_text())
    if defect=='wrong_display': chart['series'][0]['values']=[999,999,999]
    elif defect=='missing_display': chart['series'][0].pop('values')
    elif defect=='one_period':
        chart['periods']=chart['periods'][:1]
        chart['series'][0]['values']=chart['series'][0]['values'][:1]
        chart['series'][0]['raw_values']=chart['series'][0]['raw_values'][:1]
    elif defect=='extra_period':
        chart['periods'].append('2026-04')
        chart['series'][0]['values'].append(None)
        chart['series'][0]['raw_values'].append(None)
    elif defect=='normalized': chart['spec']['normalize']='index100'
    elif defect=='wrong_unit': chart['series'][0].update(unit='USD',raw_unit='USD')
    else: chart['series'].append(copy.deepcopy(chart['series'][0]))
    saved.receipt(turn,'charts',chart)
    assert grade_turn(tmp_path,'new_source',turn)['artifact_grade']=='fail'


@pytest.mark.parametrize('mode',['wrong_payload_url','tampered_manifest_url','missing_delivered_facts','wrong_delivered_date','wrong_delivered_direction'])
def test_web_authority_and_final_facts_are_independently_required(tmp_path,mode):
    saved=SavedTrial(tmp_path)
    official='https://www.tcmb.gov.tr/duyurular/basin/2025/duy2025-15'
    actual='https://example.org/copied-decision.html' if mode in ('wrong_payload_url','tampered_manifest_url') else official
    facts='6 Mart 2025. Bir hafta repo faizi yüzde 45’ten yüzde 42,5’e indirildi.'
    source=saved.source(actual,'<main>'+facts+'</main>')
    message=facts+' Kaynak: '+official
    if mode=='tampered_manifest_url':
        path=saved.root/'document_sources'/saved.workspace/source['source_id']/'manifest.json'
        source={**source,'source_url':official}
        write_json(path,source)
    elif mode=='missing_delivered_facts': message='Kaynak: '+official
    elif mode=='wrong_delivered_date': message='7 Mart 2025. Bir hafta repo faizi yüzde 45’ten yüzde 42,5’e indirildi. '+official
    elif mode=='wrong_delivered_direction': message='6 Mart 2025. Faiz yüzde 42,5’ten yüzde 45’e yükseldi. '+official
    turn={'result':{'status':'completed','workspace_id':saved.workspace,'message':message,
                   'tool_results':[{'tool':'inspect_source','result':{'status':'ok',**source,'url':official}}]}}
    grade=grade_turn(tmp_path,'web',turn)
    assert grade['artifact_grade']!='pass' and not grade['task_complete']


@pytest.mark.parametrize('case,wrong_column',[('tupras_pdf','column_8'),('garanti_pdf','column_6'),('garanti_pdf','column_9')])
def test_correct_pdf_numbers_and_output_dates_cannot_override_wrong_source_columns(tmp_path,monkeypatch,case,wrong_column):
    saved=SavedTrial(tmp_path)
    oracle=ORACLES[case]
    rows=[{'period':at,'metric':role,'value':value} for role,values in oracle['values'].items() for at,value in values.items()]
    source=saved.source('https://www.'+oracle['host']+'/report.pdf','synthetic PDF coordinate negative control','application/pdf')
    monkeypatch.setitem(ORACLES,case,{**oracle,'raw_sha256':source['raw_sha256']})
    dataset,lineage=saved.pdf_dataset(rows,source,ORACLES[case],source_column=wrong_column)
    turn=saved.analysis(rows,{'value':{**money(scale=1000),'price_basis':'2025-06-30 purchasing power'}},
                        lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    grade=grade_turn(tmp_path,case,turn)
    assert grade['artifact_grade']!='pass'
    assert any(c['check'].endswith('_source_coordinate_date') and c['status']!='pass' for c in grade['checks'])


def test_pdf_provenance_cannot_be_substituted_only_in_analysis_lineage(tmp_path,monkeypatch):
    saved=SavedTrial(tmp_path)
    rows=[{'period':'2025-06-30','metric':role,'value':values['2025-06-30']} for role,values in ORACLES['tupras_pdf']['values'].items()]
    source=saved.source('https://www.tupras.com.tr/report.pdf','synthetic immutable dataset proof','application/pdf')
    monkeypatch.setitem(ORACLES,'tupras_pdf',{**ORACLES['tupras_pdf'],'raw_sha256':source['raw_sha256']})
    dataset,lineage=saved.pdf_dataset(rows,source,ORACLES['tupras_pdf'],source_column='column_8')
    lineage=copy.deepcopy(lineage)
    for origin in lineage['dataset_query']['document_provenance']['cell_origins']:
        origin['value']['candidate_column']='column_7'
    turn=saved.analysis(rows,{'value':{**money(scale=1000),'price_basis':'2025-06-30 purchasing power'}},lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    assert grade_turn(tmp_path,'tupras_pdf',turn)['artifact_grade']!='pass'


@pytest.mark.parametrize('defect',[None,'missing_group','null_as_zero','wrong_presence'])
def test_grouped_chart_requires_every_saved_group_and_preserves_source_nulls(tmp_path,defect):
    saved=SavedTrial(tmp_path)
    rows=[{'period':'2026-01','group':'A','value':10},{'period':'2026-02','group':'A','value':None},
          {'period':'2026-02','group':'B','value':20}]
    turn=saved.analysis(rows,{'value':money(),'group':{'kind':'dimension'}})
    path=saved.chart(turn,'line',['value'],'group')
    chart=json.loads(path.read_text())
    chart['series'][0]['source_row_available']=[True,True]
    chart['series'][1]['source_row_available']=[False,True]
    if defect=='missing_group': chart['series']=chart['series'][:1]
    elif defect=='null_as_zero': chart['series'][0]['values'][1]=0
    elif defect=='wrong_presence': chart['series'][0]['source_row_available'][1]=False
    saved.receipt(turn,'charts',chart)
    checks=[]
    manifest=next(iter(turn['analyses'].values()))['manifest']
    chart_checks(checks,ReceiptReader(tmp_path),turn['result'],manifest,rows,'line',['value'])
    assert all(c['status']=='pass' for c in checks) is (defect is None)


def test_single_period_category_bars_cover_each_point_dimension(tmp_path):
    saved=SavedTrial(tmp_path)
    rows=[{'period':'Tüm kayıtlar','metric':'cash','value':10},{'period':'Tüm kayıtlar','metric':'assets','value':20}]
    turn=saved.analysis(rows,{'value':money(),'metric':{'kind':'dimension'}})
    saved.receipt(turn,'charts',{'spec':{'kind':'bar','normalize':'none'},'group_mode':'categories',
                                'point_dimensions':[{'metric':'cash'},{'metric':'assets'}],
                                'periods':['Tüm kayıtlar','Tüm kayıtlar'],'complete':True,
                                'series':[{'column':'value','raw_values':[10,20],'values':[10,20],
                                           'unit':'milyon TL','raw_unit':'milyon TL'}]})
    checks=[]
    chart_checks(checks,ReceiptReader(tmp_path),turn['result'],next(iter(turn['analyses'].values()))['manifest'],rows,'bar',['value'])
    assert all(c['status']=='pass' for c in checks)


def test_correct_sector_values_do_not_override_explicit_wrong_population_contract(tmp_path):
    saved,original=new_source_trial(tmp_path)
    prior=next(iter(original['analyses'].values()))
    lineage={**prior['manifest']['lineage'],'sources':{'sector':{'binding':{
        'population_scope':{'id':'bddk_monthly_restricted_reporting_population','exclusions':[{'institution':'Example excluded bank'}]},
        'measurement_basis':'source_reported'}}}}
    turn=saved.analysis(prior['rows'],prior['manifest']['schema'],lineage=lineage,datasets=prior['manifest']['datasets'])
    saved.chart(turn,'line',['share'])
    grade=grade_turn(tmp_path,'new_source',turn)
    assert grade['artifact_grade']=='fail'
    assert next(c for c in grade['checks'] if c['check']=='sector_population_and_measurement_basis')['status']=='fail'


@pytest.mark.parametrize('align_origins',[False,True])
def test_pdf_source_origins_must_follow_immutable_dataset_sort_order(tmp_path,monkeypatch,align_origins):
    saved=SavedTrial(tmp_path)
    oracle=ORACLES['garanti_pdf']
    input_rows=[{'period':at,'metric':role,'value':value} for role,values in oracle['values'].items() for at,value in values.items()]
    source=saved.source('https://www.garantibbvainvestorrelations.com/report.pdf','synthetic unsorted source cells','application/pdf')
    monkeypatch.setitem(ORACLES,'garanti_pdf',{**oracle,'raw_sha256':source['raw_sha256']})
    original,_=saved.pdf_dataset(input_rows,source,ORACLES['garanti_pdf'])
    order=sorted(range(len(input_rows)),key=lambda i:(input_rows[i]['period'],input_rows[i]['metric']))
    rows=[input_rows[i] for i in order]
    proof=copy.deepcopy(original['contract']['document_provenance'])
    if align_origins: proof['cell_origins']=[proof['cell_origins'][i] for i in order]
    dataset=saved.object('datasets',rows,{'contract':{'document_provenance':proof}})
    cells={json.dumps([row['period'],row['metric']]):{'value':{'source_column':'value','source_rows':[i],
            'source_values':[row['value']]}} for i,row in enumerate(rows,1)}
    lineage={'group_by':'metric','dataset_query':{'dataset_id':dataset['dataset_id'],'document_provenance':proof,
             'output_keys':['period','metric'],'cells':cells}}
    turn=saved.analysis(rows,{'value':money(scale=1000)},lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    assert (grade_turn(tmp_path,'garanti_pdf',turn)['artifact_grade']=='pass') is align_origins


def test_fixed_monthly_oracle_matches_original_bddk_raw_download_cells():
    raw=Path(__file__).resolve().parents[2]/'data_pipeline/bddk/monthly_all_groups/raw'
    if not raw.is_dir(): pytest.skip('Original BDDK downloaded sources are not installed')
    for year in ('2025','2026'):
        previous=0
        for month in range(1,7):
            paths=[p for p in raw.glob(f'{year}-{month:02d}_table02_groups*.json') if not p.name.endswith('_info.json')]
            assert len(paths)==1
            assert digest(paths[0])==ORACLES['profit']['raw_sha256'][year][month-1]
            document=json.loads(paths[0].read_bytes())['Json']
            if isinstance(document,str): document=json.loads(document)
            cells=[row['cell'] for row in document['data']['rows'] if row['cell'][0]=='Sektör' and str(row['cell'][1])=='53']
            assert len(cells)==1
            cumulative=cells[0][-1]
            assert cumulative==ORACLES['profit']['cumulative'][year][month-1]
            assert cumulative-previous==ORACLES['profit']['monthly'][year][month-1]
            previous=cumulative


def test_scheduled_missing_trial_is_not_silently_excluded(tmp_path):
    write_json(tmp_path/'manifest.json',{'cases':{'profit':['task']},'repeats':2})
    result=grade_round(tmp_path)
    assert len(result['trials'])==2
    assert all(trial['artifact_grade']=='not_run' for trial in result['trials'])


def pdf_sector_trial(path, monkeypatch, defect=None):
    """Invented source bytes carry the real oracle's shape, never financial proof."""
    saved=SavedTrial(path)
    oracle=copy.deepcopy(ORACLES['pdf_sector'])
    source=saved.source('https://www.garantibbvainvestorrelations.com/report.pdf',
                        'synthetic TOTAL ASSETS source control', 'application/pdf')
    oracle['raw_sha256']=source['raw_sha256']
    raw=path/'original-bddk.json'
    raw_rows=[{'cell':[]} for _ in range(25)]+[{'cell':['Sektör',26,'TOPLAM AKTİFLER','bold',0,0,oracle['sector_million_TRY']]}]
    write_json(raw,{'Json':{'caption':'Bilanço (milyon TL), Dönem:2026/3','data':{'rows':raw_rows}}})
    oracle.update(sector_source_file=str(raw),sector_raw_sha256=digest(raw))
    monkeypatch.setitem(ORACLES,'pdf_sector',oracle)
    input_rows=[{'period':oracle['date'],'metric':'total_assets','value':oracle['bank_thousand_TRY']}]
    dataset,_=saved.pdf_dataset(input_rows,source,oracle,
                                source_column='column_6' if defect=='wrong_TOTAL_column' else None)
    contract=copy.deepcopy(dataset['contract'])
    contract['columns']={'value':money(scale=1000)}
    if defect=='wrong_subtotal_row':
        contract['document_provenance']['cell_origins'][0]['value']['candidate_row']=12
    dataset=saved.object('datasets',input_rows,{'contract':contract})
    if defect=='missing_imported_source':
        (saved.root/'document_sources'/saved.workspace/source['source_id']/'raw.bin').unlink()
    period='2026-04' if defect=='wrong_output_date' else oracle['period']
    rows=[{'period':period,'bank_raw':oracle['bank_thousand_TRY'],
           'bank_million':float(oracle['bank_million_TRY']),'sector':oracle['sector_million_TRY'],
           'percent':float(oracle['percent'])}]
    schema={'bank_raw':money(scale=1000,metric='overlay:'+dataset['dataset_id']+':value'),
            'bank_million':money(metric='overlay:'+dataset['dataset_id']+':value'),
            'sector':money(metric=oracle['sector_metric_id']),
            'percent':{'unit':'percent','kind':'ratio','scale':1}}
    bank={'binding':{'dataset_id':dataset['dataset_id'],'document_provenance':contract['document_provenance'],
                     'time_column':'period','value_column':'value'},'dimensions':{'metric':'total_assets'},
          'alignment':'period_end','cells':{period:[{'native_period':oracle['date'],
                'source_value':oracle['bank_thousand_TRY'],'computed_value':oracle['bank_thousand_TRY']}]}}
    sector={'binding':{'metric_id':oracle['sector_metric_id'],'population_scope':{
                'id':'bddk_monthly_full_reporting_population','exclusions':[]},'measurement_basis':'source_reported'},
            'dimensions':{'group_code':10001},'cells':{period:[{
                'native_period':oracle['period'],'group_code':10001,'source_file':str(raw),
                'source_sha256':oracle['sector_raw_sha256'],'source_row_index':26,'value_dimension':'Toplam',
                'source_value':oracle['sector_million_TRY'],'computed_value':oracle['sector_million_TRY']}]}}
    operations=[{'op':'scale','column':'bank_raw','target_scale':1000000,'output':'bank_million'},
                {'op':'ratio','column':'bank_million','denominator':'sector','output':'percent','multiplier':100,
                 'scope_policy':'explicit_comparison','scope_reason':'Consolidated group and BDDK banking population differ.'}]
    if defect=='wrong_unit': schema['bank_million']['scale']=1000
    elif defect=='wrong_population': sector['binding']['population_scope']['id']='restricted_reporting_population'
    elif defect=='wrong_bddk_cell': sector['cells'][period][0]['source_row_index']=25
    elif defect=='wrong_bddk_hash': sector['cells'][period][0]['source_sha256']='wrong'
    elif defect=='unbound_conversion': operations=operations[1:]
    elif defect=='ratio_without_explicit_scope': operations[-1]['scope_policy']='same_scope'
    elif defect=='wrong_ratio': rows[0]['percent']*=1000
    turn=saved.analysis(rows,schema,lineage={'sources':{'bank_raw':bank,'sector':sector},'operations':operations,
                                            'join_contract':{'population_equivalence_asserted':False}},
                        datasets=[dataset['dataset_id']])
    turn['result']['message']='Garanti konsolide grup ile BDDK sektör kapsamı birebir aynı değildir. Bu oran resmi pazar payı değildir.'
    if defect=='wrong_scope_prose':
        turn['result']['message']='Garanti konsolide grup ile BDDK sektör kapsamı aynıdır. Bu oran resmi pazar payıdır.'
    saved.chart(turn,'bar',['percent'])
    if defect=='wrong_chart_value':
        chart=read_json(saved.root/'charts'/saved.workspace/(turn['result']['chart_id']+'.json'))
        chart['series'][0]['values'][0]=99
        saved.receipt(turn,'charts',chart)
    return saved,turn


@pytest.mark.parametrize('defect',[None,'wrong_TOTAL_column','wrong_subtotal_row','wrong_unit','wrong_output_date',
                                  'wrong_population','missing_imported_source','wrong_bddk_cell','wrong_bddk_hash',
                                  'unbound_conversion','ratio_without_explicit_scope','wrong_ratio',
                                  'wrong_scope_prose','wrong_chart_value'])
def test_pdf_sector_needs_exact_two_source_cells_scale_scope_and_delivered_chart(tmp_path,monkeypatch,defect):
    _,turn=pdf_sector_trial(tmp_path,monkeypatch,defect)
    grade=grade_turn(tmp_path,'pdf_sector',turn)
    assert (grade['artifact_grade']=='pass') is (defect is None),grade['checks']
    assert grade['task_complete'] is (defect is None)


def test_pdf_sector_fixed_denominator_matches_original_download():
    oracle=ORACLES['pdf_sector']
    path=Path(__file__).resolve().parents[2]/oracle['sector_source_file']
    if not path.is_file(): pytest.skip('Original BDDK downloaded source is not installed')
    assert digest(path)==oracle['sector_raw_sha256']
    document=json.loads(path.read_bytes())['Json']
    if isinstance(document,str): document=json.loads(document)
    cell=document['data']['rows'][25]['cell']
    assert cell[0]=='Sektör' and cell[1]==26 and cell[2]=='TOPLAM AKTİFLER' and cell[6]==49735194
    assert float(oracle['bank_million_TRY'])/cell[6]*100==pytest.approx(float(oracle['percent']),rel=1e-14)


def test_total_assets_role_is_not_changed_by_statement_of_financial_position_caption(tmp_path,monkeypatch):
    saved=SavedTrial(tmp_path)
    oracle=ORACLES['tupras_pdf']
    rows=[{'period':'Tüm kayıtlar','metric':role,'value':values['2025-06-30']}
          for role,values in oracle['values'].items()]
    source=saved.source('https://www.tupras.com.tr/report.pdf','synthetic statement title role control','application/pdf')
    monkeypatch.setitem(ORACLES,'tupras_pdf',{**oracle,'raw_sha256':source['raw_sha256']})
    dated=[{**row,'period':'2025-06-30'} for row in rows]
    dataset,lineage=saved.pdf_dataset(dated,source,ORACLES['tupras_pdf'])
    lineage['dataset_query']['cells']={json.dumps(['Tüm kayıtlar',json.loads(key)[1]]):value
                                      for key,value in lineage['dataset_query']['cells'].items()}
    turn=saved.analysis(rows,{'value':{**money(scale=1000),'price_basis':'30 June 2025',
                          'source_semantics':'Interim consolidated statement of financial position, amounts in thousands of TRY.'}},
                        lineage=lineage,datasets=[dataset['dataset_id']])
    saved.chart(turn,'bar',['value'],'metric')
    grade=grade_turn(tmp_path,'tupras_pdf',turn)
    assert grade['artifact_grade']=='pass',grade['checks']


@pytest.mark.parametrize('message,expected',[
    ('Bu oran farklı kurum veya raporlama kapsamları arasında sayısal büyüklük karşılaştırmasıdır. '
     'Kaynakların aynı nüfusu temsil ettiği veya geçerli bir pay-payda ilişkisi kurduğu doğrulanmamıştır; '
     'bu nedenle resmi sektör/pazar payı değildir.',(True,True)),
    ('The reporting populations differ. This comparison is not official market share.',(True,True)),
    ('Raporlama kapsamları farklıdır. Bu oran resmi pazar payı değildir.',(True,True)),
    ('Kaynaklar farklı. Bu oran resmi pazar payı değildir.',(False,True)),
    ('Raporlama kapsamları farklıdır.',(True,False)),
    ('Raporlama kapsamları aynıdır. Bu oran resmi pazar payıdır.',(False,False)),
])
def test_scope_disclosure_requires_reporting_population_not_institution_name(message,expected):
    assert pdf_sector_scope_disclosures(message)==expected
