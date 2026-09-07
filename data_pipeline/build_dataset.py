"""Build audited native observations and monthly analytics from frozen raw files.

Run: python build_dataset.py
No network or LLM is needed. Metadata is configuration; no source is silently filled.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
TARGET_START, TARGET_END = '2021-01', '2026-06'
WARMUP_START = '2020-01'

def build(root=ROOT, write=True):
    root = Path(root)
    registry = json.loads((root/'series_registry.json').read_text())
    calendar = pd.period_range(WARMUP_START, TARGET_END, freq='M').astype(str)
    monthly = pd.DataFrame(index=pd.Index(calendar, name='month'))
    long_parts, weekly_parts, checks = [], [], []
    raw_cache = {}
    for spec in registry:
        filename = spec['raw_file']
        data_bytes = (root/'raw'/filename).read_bytes()
        if hashlib.sha256(data_bytes).hexdigest() != spec['raw_sha256']:
            raise ValueError(f'Raw checksum mismatch: {filename}')
        if filename not in raw_cache:
            raw_cache[filename] = json.loads(data_bytes)
        response = raw_cache[filename]
        data = pd.DataFrame(response['items'])
        if len(data) != response['totalCount']:
            raise ValueError(f'Response count mismatch: {filename}')
        native = pd.to_numeric(data[spec['raw_column']], errors='raise')
        if native.isna().any() or not np.isfinite(native).all():
            raise ValueError(f'Missing or nonfinite values: {spec["series_code"]}')
        if (native < 0).any():
            raise ValueError(f'Negative value in selected nonnegative measure: {spec["series_code"]}')
        weekly = spec['native_frequency']=='weekly_friday'
        dates = pd.to_datetime(data['Tarih'], format='%d-%m-%Y' if weekly else '%Y-%m')
        if dates.duplicated().any():
            raise ValueError(f'Duplicate dates: {spec["series_code"]}')
        keep = (dates >= pd.Timestamp('2020-01-01')) & (dates <= pd.Timestamp('2026-06-30'))
        frame = pd.DataFrame({'observation_date':dates[keep], 'value':native[keep]}).sort_values('observation_date')
        frame['series_code']=spec['series_code']
        frame['native_unit']=spec['native_unit']
        frame['native_frequency']=spec['native_frequency']
        frame['source_sha256']=spec['raw_sha256']
        frame['vintage']=spec['observed_vintage']
        long_parts.append(frame)
        info = dict(series_code=spec['series_code'],raw_rows=len(data),kept_rows=len(frame),
            dropped_outside_period=data.loc[~keep,'Tarih'].tolist(),missing=0,duplicates=0,
            first=str(frame.observation_date.min().date()),last=str(frame.observation_date.max().date()))
        values = pd.Series(frame.value.to_numpy(),index=frame.observation_date)
        if weekly:
            expected = pd.date_range('2020-01-01','2026-06-30',freq='W-FRI')
            if not values.index.equals(expected): raise ValueError('Missing or unexpected weekly date')
            w = frame[['observation_date','value']].rename(columns={'value':'housing_rate_annual_pct'})
            w['month']=w.observation_date.dt.to_period('M').astype(str)
            weekly_parts.append(w)
            grouped=w.groupby('month')
            stats=grouped.housing_rate_annual_pct.agg(['mean','last','min','max','count'])
            stats.columns=['housing_rate_annual_pct_weekly_mean','housing_rate_annual_pct_last',
                'housing_rate_annual_pct_min','housing_rate_annual_pct_max','housing_rate_week_count']
            stats['housing_rate_last_observation_date']=grouped.observation_date.max().dt.strftime('%Y-%m-%d')
            monthly=monthly.join(stats,how='left',validate='one_to_one')
        else:
            values.index=values.index.to_period('M').astype(str)
            if not values.index.equals(pd.Index(calendar)):
                raise ValueError(f'Incomplete monthly coverage: {spec["series_code"]}')
            values=values*spec.get('output_scale',1)
            if spec['measurement']=='flow' and not np.equal(values,values.round()).all():
                raise ValueError('Noninteger count')
            monthly=monthly.join(values.rename(spec['output_column']),validate='one_to_one')
        checks.append(info)
    native=pd.concat(long_parts,ignore_index=True)
    assert not native.duplicated(['series_code','observation_date']).any()
    native=native.sort_values(['series_code','observation_date']).reset_index(drop=True)
    if monthly.isna().any().any(): raise ValueError('Missing joined source values')
    # Independent source identity: first-hand + second-hand = total.
    sales_ok=monthly.housing_sales_total_count.eq(monthly.housing_sales_first_hand_count+monthly.housing_sales_second_hand_count)
    assert sales_ok.all()
    assert monthly.housing_sales_mortgaged_count.le(monthly.housing_sales_total_count).all()
    consumer_identities={}
    for bank in ['deposit','development_investment','participation']:
        # Verify from values, not the catalog's sometimes inconsistent parent nesting.
        total=monthly[f'consumer_credit_{bank}_stock_million_tl']
        components=monthly[[f'{k}_credit_{bank}_stock_million_tl' for k in ['housing','vehicle','needs_other']]].sum(axis=1)
        matched=np.isclose(total,components,rtol=0,atol=1e-8)
        assert matched.all(), f'Consumer components mismatch: {bank}'
        consumer_identities[bank]=int(matched.sum())
    monthly['housing_sales_other_count_derived']=monthly.housing_sales_total_count-monthly.housing_sales_mortgaged_count
    monthly['mortgage_sales_share_pct']=100*monthly.housing_sales_mortgaged_count/monthly.housing_sales_total_count
    monthly['first_hand_sales_share_pct']=100*monthly.housing_sales_first_hand_count/monthly.housing_sales_total_count
    # These are explicit sums of the three mutually exclusive bank groups.
    banks=['deposit','development_investment','participation']
    for kind in ['housing','consumer','vehicle','needs_other','cards']:
        columns=[f'{kind}_credit_{b}_stock_million_tl' for b in banks]
        monthly[f'{kind}_credit_three_groups_stock_million_tl']=monthly[columns].sum(axis=1,min_count=3)
    monthly['housing_credit_ex_participation_stock_million_tl']=monthly[[
        'housing_credit_deposit_stock_million_tl','housing_credit_development_investment_stock_million_tl']].sum(axis=1,min_count=2)
    cpi_base=monthly.loc[TARGET_START,'cpi_index2025']
    stock='housing_credit_three_groups_stock_million_tl'
    monthly['housing_credit_real_jan2021_million_tl']=monthly[stock]*cpi_base/monthly.cpi_index2025
    for region in ['tr','istanbul','ankara','izmir']:
        c=f'kfe_{region}_index2023'
        relative=monthly[c]/monthly.cpi_index2025
        monthly[f'kfe_{region}_real_jan2021_100']=100*relative/relative.loc[TARGET_START]
    for col,prefix in [('cpi_index2025','cpi'),('kfe_tr_index2023','kfe_tr'),
        (stock,'housing_credit_nominal'),('housing_credit_real_jan2021_million_tl','housing_credit_real'),
        ('housing_sales_total_count','housing_sales_total'),('housing_sales_mortgaged_count','housing_sales_mortgaged')]:
        monthly[prefix+'_mom_pct']=monthly[col].pct_change(fill_method=None)*100
        monthly[prefix+'_yoy_pct']=monthly[col].pct_change(12,fill_method=None)*100
    monthly['housing_credit_net_stock_change_million_tl']=monthly[stock].diff()
    monthly['housing_rate_change_pp']=monthly.housing_rate_annual_pct_weekly_mean.diff()
    monthly['rate_down_real_stock_not_up']=(monthly.housing_rate_change_pp < 0) & (monthly.housing_credit_real_mom_pct <= 0)
    # Descriptive co-occurrence only. Currency and bank scope do not perfectly match rate.
    target=monthly.loc[TARGET_START:TARGET_END].copy()
    assert len(target)==66 and target.index.is_unique
    assert not target.isna().any().any()
    assert abs(monthly.loc['2025-01':'2025-12','cpi_index2025'].mean()-100)<1e-6
    for region in ['tr','istanbul','ankara','izmir']:
        assert abs(monthly.loc['2023-01':'2023-12',f'kfe_{region}_index2023'].mean()-100)<0.006
        assert abs(target.loc[TARGET_START,f'kfe_{region}_real_jan2021_100']-100)<1e-10
    assert abs(target.loc[TARGET_START,'housing_credit_real_jan2021_million_tl']-target.loc[TARGET_START,stock])<1e-8
    weekly=pd.concat(weekly_parts,ignore_index=True)
    checks_summary=dict(status='passed',source_series=len(registry),native_observations_with_warmup=len(native),
        target_months=len(target),warmup_months=12,source_nulls=0,duplicate_keys=0,
        target_columns=len(target.columns),source_sales_identity_matches=int(sales_ok.sum()),
        consumer_component_identity_matches=consumer_identities,
        weekly_target_observations=int((weekly.observation_date>=pd.Timestamp('2021-01-01')).sum()),
        checks=checks,
        limits=['Not every historical rate/KFE/credit/sales point was matched to a second publication.',
        'Current-vintage data, not historical release-time data.',
        'TCMB credit statistics are not BDDK bulletin exports.',
        'Stock change is not new lending; co-occurrence is not causality.'])
    if write:
        out=root/'processed';out.mkdir(exist_ok=True)
        native.to_parquet(out/'observations_native.parquet',index=False)
        weekly.to_csv(out/'housing_rate_weekly.csv',index=False,float_format='%.10g')
        monthly.reset_index().to_parquet(out/'monthly_with_warmup.parquet',index=False)
        target.reset_index().to_parquet(out/'monthly_2021_2026.parquet',index=False)
        target.to_csv(out/'monthly_2021_2026.csv',float_format='%.12g')
        with sqlite3.connect(out/'analytics.sqlite') as conn:
            target.reset_index().to_sql('monthly_analytics',conn,if_exists='replace',index=False)
            native.to_sql('observations_native',conn,if_exists='replace',index=False)
            pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in registry]).to_sql('series_registry',conn,if_exists='replace',index=False)
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS month_unique ON monthly_analytics(month)')
            event_file=root/'evidence/events/events.json'
            if event_file.exists():
                events=json.loads(event_file.read_text())
                event_table=pd.DataFrame([{k:v for k,v in event.items() if not isinstance(v,(dict,list))} for event in events])
                event_table.to_sql('context_events',conn,if_exists='replace',index=False)
                event_table.to_csv(out/'context_events.csv',index=False)
                # Separate one-to-many annotation view preserves one monthly numeric row.
                conn.execute('DROP VIEW IF EXISTS monthly_with_context')
                conn.execute('CREATE VIEW monthly_with_context AS SELECT m.*, (SELECT group_concat(e.event_id, " | ") FROM context_events e WHERE e.monthly_annotation_key=m.month) AS context_event_ids FROM monthly_analytics m')
        (out/'validation.json').write_text(json.dumps(checks_summary,ensure_ascii=False,indent=2))
    return target,monthly,native,checks_summary

if __name__=='__main__':
    target,_,_,report=build()
    print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False,indent=2))
