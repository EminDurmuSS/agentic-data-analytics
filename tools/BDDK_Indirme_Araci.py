#!/usr/bin/env python3
"""BDDK public monthly downloader and explicit access diagnosis (Python 3.9+).

Default: all 17 tables and all 10 public bank groups, Jan2021-Jun2026.
Standard-library only.
The observed server contract was checked against the rbrsa author's source:
https://github.com/obakis/rbrsa/blob/main/R/fetch_bddk.R
Live TLS-verified retrieval and the public 10-group request contract were
validated on 2026-09-08.
"""
import argparse
import csv
from collections import Counter
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

MONTHLY_PATH='/BultenAylik/tr/Home/BasitRaporGetir'
HEADERS={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
         'Accept':'application/json','X-Requested-With':'XMLHttpRequest',
         'User-Agent':'BDDK-Public-Data-Research/1.0'}

# Verified from the live public monthly page on 2026-09-08. The monthly and
# weekly applications use different meanings for some of the same codes, so
# these mappings intentionally remain local to each downloader.
GROUPS = {
    10001: 'Sektör',
    10002: 'Mevduat',
    10003: 'Katılım',
    10004: 'Kalkınma ve Yatırım',
    10005: 'Yerli Özel',
    10006: 'Kamu',
    10007: 'Yabancı',
    10008: 'Mevduat-Yerli Özel',
    10009: 'Mevduat-Kamu',
    10010: 'Mevduat-Yabancı',
}

def now(): return datetime.now(timezone.utc).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()
def write_json(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def months(start,end):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',start) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',end):
        raise ValueError('Tarih YYYY-AA olmali.')
    y,m=map(int,start.split('-'));last=tuple(map(int,end.split('-')))
    if (y,m)>last: raise ValueError('Baslangic bitisten sonra.')
    result=[]
    while (y,m)<=last:
        result.append(f'{y:04}-{m:02}');y,m=(y+1,1) if m==12 else (y,m+1)
    return result

def parse_response(data):
    outer=json.loads(data.decode('utf-8-sig'))
    if not isinstance(outer,dict) or outer.get('success') is not True:
        raise ValueError('BDDK success=true yaniti yok; veri olarak kabul edilmedi.')
    inner=outer.get('Json')
    for _ in range(2):
        if isinstance(inner,str):inner=json.loads(inner)
    if isinstance(inner,dict) and {'colModels','colNames','data'} <= set(inner):
        # Actual jqGrid envelope described by rbrsa/R/utils.R. Keep hidden
        # columns and original values, including the difference between null/0.
        models=inner['colModels'];labels=inner['colNames']
        source_rows=inner['data'].get('rows') if isinstance(inner['data'],dict) else None
        if not isinstance(models,list) or not models or not isinstance(labels,list) or len(models)!=len(labels):
            raise ValueError('BDDK kolon modeli/etiket sayisi uyusmuyor.')
        names=[m.get('name') or labels[i] for i,m in enumerate(models)]
        if not all(isinstance(n,str) and n for n in names) or len(set(names))!=len(names):
            raise ValueError('Bos veya tekrarlanan BDDK kolon kimligi.')
        if not isinstance(source_rows,list) or not source_rows:
            raise ValueError('BDDK data.rows bos veya gecersiz.')
        result=[]
        for row in source_rows:
            cells=row.get('cell') if isinstance(row,dict) else None
            if not isinstance(cells,list) or len(cells)!=len(names):
                raise ValueError('BDDK hucre sayisi kolon modeliyle uyusmuyor.')
            result.append(dict(zip(names,cells)))
        inner=result
    elif isinstance(inner,dict):
        candidates=[v for v in inner.values() if isinstance(v,list) and v and all(isinstance(r,dict) for r in v)]
        if len(candidates)==1:inner=candidates[0]
    if not isinstance(inner,list) or not inner or not all(isinstance(r,dict) for r in inner):
        raise ValueError('Json tablo semasi beklenenden farkli; ham yanit saklandi.')
    columns=set().union(*(r.keys() for r in inner))
    if not columns.intersection({'Ad','BasitSira'}):
        raise ValueError('Beklenen BDDK tablo alanlari bulunamadi.')
    return inner

def classify(status,data,error=''):
    body=data[:5000].decode('utf-8',errors='replace').lower()
    if 'certificate verify failed' in body and 'issuer certificate' in body:
        return 'gateway_upstream_certificate_failure'
    if any(s in error.lower() for s in ['certificate_verify_failed','certificate verify failed','ssl certificate problem']):
        return 'local_certificate_validation_failure'
    if status==429:return 'rate_limited'
    if status in (401,403):return 'access_denied_unclassified'
    if status in (502,503,504):return 'gateway_or_service_failure'
    if error:return 'network_or_transport_failure'
    if status and 200<=status<300:return 'http_success_pending_data_validation'
    return 'unexpected_http_status'

def fetch(url,body,transport,timeout,ca_bundle=None):
    started=now();data=b'';status=None;error='';content_type='';final_url=url
    if transport=='curl':
        if not shutil.which('curl'):raise RuntimeError('curl bulunamadi; --transport urllib kullanin.')
        with tempfile.TemporaryDirectory(prefix='bddk_http_') as d:
            file=Path(d)/'response.bin'
            command=['curl','--disable','--silent','--show-error','--location','--max-redirs','3',
                '--proto','=https','--proto-redir','=https','--connect-timeout',str(min(timeout,15)),
                '--max-time',str(timeout),'--output',str(file),
                '--write-out','%{http_code}\n%{url_effective}\n%{content_type}',
                '--request','POST','--data-binary','@-']
            for key,value in HEADERS.items():command+=['--header',key+': '+value]
            if ca_bundle:command+=['--cacert',str(ca_bundle)]
            command.append(url)
            try:
                result=subprocess.run(command,input=body,capture_output=True,timeout=timeout+5,check=False)
                lines=result.stdout.decode('utf-8',errors='replace').splitlines()
                if lines and lines[0].isdigit():status=int(lines[0]) or None
                if len(lines)>1:final_url=lines[1]
                if len(lines)>2:content_type=lines[2]
                if result.returncode:error=result.stderr.decode('utf-8',errors='replace')[:1000]
                if file.exists():data=file.read_bytes()
            except subprocess.TimeoutExpired:error='curl subprocess timeout'
    else:
        context=ssl.create_default_context(cafile=str(ca_bundle) if ca_bundle else None)
        request=urllib.request.Request(url,data=body,headers=HEADERS,method='POST')
        try:
            with urllib.request.urlopen(request,timeout=timeout,context=context) as response:
                data=response.read();status=response.status;content_type=response.headers.get('Content-Type','');final_url=response.url
        except urllib.error.HTTPError as exc:
            status=exc.code;data=exc.read();content_type=exc.headers.get('Content-Type','');error=str(exc)
        except Exception as exc:error=str(exc)
    record={'started_at_utc':started,'completed_at_utc':now(),'url':url,'final_url':final_url,
        'method':'POST','transport':transport,'http_status':status,'content_type':content_type,
        'sha256':digest(data),'bytes':len(data),'error':error,
        'diagnosis':classify(status,data,error),'tls_verification':True}
    if urllib.parse.urlparse(final_url).hostname not in {'www.bddk.org.tr','www.bddk.gov.tr','bddk.org.tr','bddk.gov.tr'}:
        record['diagnosis']='unexpected_redirect_host'
    return data,record

def make_bundle(output):
    bundle=output.with_name(output.name+'_Paylas.zip')
    with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(output.rglob('*')):
            if path.is_file():z.write(path,str(path.relative_to(output.parent)))
    return bundle

def request_batches(groups,mode):
    if mode=='combined':return [groups]
    return [[group] for group in groups]

def request_stem(period,table,batch):
    if len(batch)==1:return f'{period}_table{table:02}_group{batch[0]}'
    return f'{period}_table{table:02}_groups'+('-'.join(str(group) for group in batch))

def validate_requested_groups(rows,batch):
    counts=Counter(str(row.get('BankaAdi')) for row in rows)
    expected={GROUPS[group] for group in batch}
    actual=set(counts)
    if actual!=expected:
        raise ValueError(
            'BDDK cevap gruplari istekle uyusmuyor: '
            f'eksik={sorted(expected-actual)}, beklenmeyen={sorted(actual-expected)}'
        )
    if any(count<=0 for count in counts.values()):
        raise ValueError('BDDK cevap grubunda veri satiri yok.')
    return counts

def run(args):
    if args.timeout<=0 or args.retries<0 or args.delay<0 or args.checkpoint_every<=0:
        raise ValueError('Timeout ve checkpoint pozitif; retries ve delay negatif olmayan sayilar olmali.')
    output=Path(args.output).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    raw=output/'raw';raw.mkdir(exist_ok=True)
    periods=months(args.start,args.end)
    tables=list(range(1,18)) if args.all_monthly_tables else sorted(set(args.tables))
    if any(t not in range(1,18) for t in tables):raise ValueError('Aylik tablo numarasi 1..17 olmali.')
    # Combined mode reduces the full all-group scope to one request per
    # table-period. Every returned row is still attributed and validated by
    # the exact public BankaAdi label.
    groups=sorted(set(args.groups))
    if any(g not in GROUPS for g in groups):
        raise ValueError('Aylik banka grubu kodu resmi katalogdaki 10001..10010 kodlarindan biri olmali.')
    if args.probe:periods=[args.end];tables=[4];groups=[10001]
    batches=request_batches(groups,args.request_mode)
    config={'start':args.start,'end':args.end,'period_count':len(periods),'tables':tables,
        'groups':{str(group):GROUPS[group] for group in groups},'reporting_currency':'TL','host':args.host,'transport':args.transport,
        'request_mode':args.request_mode,
        'scope':'BDDK monthly public tables; not weekly or FinTurk',
        'unit_policy':'Preserve source columns and jqGrid identifiers; labels/units stay in raw Json. Validate table-specific units against export metadata.',
        'tls_verification':True,
        'group_catalog_source':'https://www.bddk.org.tr/BultenAylik/tr/',
        'group_catalog_verified_at':'2026-09-08',
        'contract_source':'https://github.com/obakis/rbrsa/blob/main/R/fetch_bddk.R'}
    config_file=output/'request_config.json'
    if config_file.exists() and json.loads(config_file.read_text(encoding='utf-8'))!=config:
        raise ValueError('Bu klasor farkli secimlerle kullanilmis; yeni --output klasoru secin.')
    write_json(config_file,config)
    records=[];combined=[];failure=False
    for period in periods:
        for table in tables:
            for batch in batches:
                name=request_stem(period,table,batch)
                response_path=raw/(name+'.json');info_path=raw/(name+'_info.json')
                cached=False
                if response_path.exists() and info_path.exists():
                    info=json.loads(info_path.read_text(encoding='utf-8'));data=response_path.read_bytes()
                    cached=(info.get('status')=='validated' and info.get('sha256')==digest(data)
                        and [int(value) for value in info.get('group_codes',[])]==batch)
                if not cached:
                    year,month=period.split('-')
                    form=[('tabloNo',str(table)),('yil',year),('ay',str(int(month))),('paraBirimi','TL')]
                    form.extend(('taraf',str(group)) for group in batch)
                    url='https://'+args.host+MONTHLY_PATH
                    body=urllib.parse.urlencode(form).encode()
                    attempt_history=[]
                    for attempt in range(1,args.retries+2):
                        data,info=fetch(url,body,args.transport,args.timeout,args.ca_bundle)
                        info.update(period=period,table_no=table,group_codes=batch,
                            group_names={str(group):GROUPS[group] for group in batch},form=form,attempt=attempt)
                        attempt_history.append({
                            'attempt':attempt,
                            'http_status':info.get('http_status'),
                            'diagnosis':info.get('diagnosis'),
                            'error':info.get('error'),
                            'bytes':info.get('bytes'),
                        })
                        retryable=(
                            info.get('http_status') in (429,500,502,503,504)
                            or info.get('diagnosis') in {
                                'network_or_transport_failure',
                                'gateway_or_service_failure',
                            }
                        )
                        if not retryable or attempt>args.retries:break
                        time.sleep(min(2**attempt,8))
                    info['attempt_history']=attempt_history
                    response_path.write_bytes(data)
                try:
                    if info.get('diagnosis')=='unexpected_redirect_host':raise ValueError('Beklenmeyen yonlendirme.')
                    if not info.get('http_status') or not 200<=info['http_status']<300:
                        raise ValueError(info.get('diagnosis','HTTP hatasi'))
                    rows=parse_response(data)
                    group_counts=validate_requested_groups(rows,batch)
                    # BasitSira is a display order, not a guaranteed primary key. BDDK
                    # occasionally publishes two distinct labels with the same order.
                    # Preserve both rows and reject only a duplicate composite identity.
                    keys=[(str(row.get('BankaAdi')),str(row.get('BasitSira')),str(row.get('Ad'))) for row in rows]
                    if len(keys)!=len(set(keys)):
                        raise ValueError('Ayni tabloda tekrarlanan BankaAdi/BasitSira/Ad kimligi.')
                    order_values=[str(row.get('BasitSira')) for row in rows if row.get('BasitSira') is not None]
                    duplicate_orders=sorted({value for value in order_values if order_values.count(value)>1})
                    info.update(
                        status='validated',
                        diagnosis='validated_bddk_table',
                        rows=len(rows),
                        rows_by_group=dict(sorted(group_counts.items())),
                        duplicate_display_orders=duplicate_orders,
                        served_from_cache=cached,
                    )
                    for row_index,row in enumerate(rows, start=1):
                        group_name=str(row.get('BankaAdi'))
                        group_code=next(code for code,name in GROUPS.items() if name==group_name)
                        combined.append({'_requested_period':period,'_requested_table_no':table,
                            '_requested_group_code':group_code,'_requested_group_name':group_name,
                            '_source_row_index':row_index,**row})
                    print(f'{period} tablo={table} gruplar={len(batch)}: {len(rows)} satir'+(' (kayitli)' if cached else ''),flush=True)
                except Exception as exc:
                    info.update(status='failed',validation_error=str(exc));failure=True
                    print('DURDU:',info.get('diagnosis'),str(exc),flush=True)
                write_json(info_path,info);records.append(info)
                if failure or len(records) % args.checkpoint_every == 0:
                    write_json(output/'manifest.json',records)
                if failure:break
                if not cached:time.sleep(args.delay)
            if failure:break
        if failure:break
    write_json(output/'manifest.json',records)
    if combined and args.write_combined_csv:
        columns=list(dict.fromkeys(k for row in combined for k in row))
        with (output/'monthly_rows.csv').open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=columns);writer.writeheader();writer.writerows(combined)
    expected=len(periods)*len(tables)*len(batches)
    successes=sum(r.get('status')=='validated' for r in records)
    summary={'status':'complete' if successes==expected and not failure else 'incomplete',
        'validation_level':'HTTP and schema checks; requested period/group and source units still need export metadata verification.',
        'expected_requests':expected,'successful_requests':successes,'rows':len(combined),
        'group_count':len(groups),'groups':{str(group):GROUPS[group] for group in groups},
        'request_mode':args.request_mode,
        'combined_csv_written':bool(args.write_combined_csv),
        'missing_requests':expected-successes,'first_failure':next((r for r in records if r.get('status')=='failed'),None),
        'notes':['HTTP200 alone is not data validation.','No filling, no TLS bypass, no claim of bank-scope equivalence with TCMB.']}
    write_json(output/'summary.json',summary)
    print('\nSonuc:',summary['status'],f'({successes}/{expected} istek)')
    if args.bundle:
        bundle=make_bundle(output)
        print('Paylasilabilir dosya:',bundle)
    if failure:
        diagnosis=summary['first_failure'].get('diagnosis')
        if diagnosis=='gateway_upstream_certificate_failure':
            print('Ara ag katmani BDDK sertifika zincirini dogrulayamadi. Yerel CA dosyasini degistirmek bu ara katmani onarmaz.')
        elif diagnosis=='local_certificate_validation_failure':
            print('Yerel sertifika deposu hatasi. Isletim sisteminin guvenilir CA deposunu onarin veya --transport curl deneyin.')
        print('Veri indirilmis gibi isaretlenmedi. summary.json ve manifest.json taniyi icerir.')
    return 1 if failure else 0

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start',default='2021-01');parser.add_argument('--end',default='2026-06')
    parser.add_argument('--tables',nargs='+',type=int,default=list(range(1,18)))
    parser.add_argument('--groups',nargs='+',type=int,default=sorted(GROUPS))
    parser.add_argument('--request-mode',choices=['combined','separate'],default='combined')
    parser.add_argument('--all-monthly-tables',action='store_true')
    parser.add_argument('--probe',action='store_true',help='Only June2026 consumer-credit sector table (or --end).')
    parser.add_argument('--host',choices=['www.bddk.org.tr','www.bddk.gov.tr'],default='www.bddk.org.tr')
    parser.add_argument('--transport',choices=['curl','urllib'],default='curl' if shutil.which('curl') else 'urllib')
    parser.add_argument('--timeout',type=int,default=30)
    parser.add_argument('--retries',type=int,default=4)
    parser.add_argument('--delay',type=float,default=0.6)
    parser.add_argument('--checkpoint-every',type=int,default=50)
    parser.add_argument('--write-combined-csv',action='store_true')
    parser.add_argument('--bundle',action='store_true')
    parser.add_argument('--ca-bundle',type=Path,help='Optional already trusted PEM CA bundle; verification remains enabled.')
    parser.add_argument('--output',default='data_pipeline/bddk/monthly_all_groups')
    args=parser.parse_args()
    try:return run(args)
    except Exception as exc:print('HATA:',exc,file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
