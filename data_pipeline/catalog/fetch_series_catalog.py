import concurrent.futures
import csv
import datetime
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / 'groups'
DEST.mkdir(exist_ok=True)
groups = list(csv.DictReader((ROOT / 'evds_groups.csv').open(encoding='utf-8')))
groups.sort(key=lambda r: (r['is_archive'] == 'True', r['group_code']))
manifest_path = ROOT / 'series_catalog_manifest.jsonl'
stop = threading.Event()
lock = threading.Lock()
start = time.monotonic()
last_start = 0.0
bytes_downloaded = 0
cached = {}
if manifest_path.exists():
    for line in manifest_path.read_text(encoding='utf-8').splitlines():
        rec = json.loads(line)
        if rec.get('result') == 'success':
            path = ROOT / rec['file']
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == rec.get('sha256'):
                cached[rec['group_code']] = rec
results = list(cached.values())
cached_count = len(results)

def fetch(g):
    global last_start, bytes_downloaded
    if stop.is_set():
        return None
    with lock:
        if time.monotonic() - start > 300 or bytes_downloaded > 100 * 1024 * 1024:
            stop.set()
            return None
        delay = max(0, .2 - (time.monotonic() - last_start))
        if delay:
            time.sleep(delay)
        last_start = time.monotonic()
    code = g['group_code']
    url = 'https://evds3.tcmb.gov.tr/igmevdsms-dis/serieList/fe/type=json&code=' + code
    record = {'group_code': code, 'url': url, 'method': 'GET', 'archive': g['is_archive'] == 'True'}
    try:
        response = urllib.request.urlopen(url, timeout=15)
        body = response.read()
        value = json.loads(body)
        if not isinstance(value, list):
            raise ValueError('Expected the frontend series catalog JSON array')
        mismatched = [x.get('SERIE_CODE') for x in value if x.get('DATAGROUP_CODE') != code]
        missing_codes = sum(not x.get('SERIE_CODE') for x in value)
        if mismatched or missing_codes:
            raise ValueError(f'Metadata mismatch: {len(mismatched)} group mismatches, {missing_codes} missing codes')
        filename = f'groups/{code}.json'
        (ROOT / filename).write_bytes(body)
        record.update({'http_status': response.status, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'file': filename, 'series_count': len(value), 'result': 'success'})
        with lock:
            bytes_downloaded += len(body)
    except urllib.error.HTTPError as exc:
        record.update({'http_status': exc.code, 'result': 'http_error', 'error': str(exc)})
        if exc.code in (403, 429):
            stop.set()
    except Exception as exc:
        record.update({'result': 'error', 'error': f'{type(exc).__name__}: {exc}'})
    record['downloaded_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with lock:
        with manifest_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    pending = {pool.submit(fetch, g) for g in groups if g['group_code'] not in cached}
    for fut in concurrent.futures.as_completed(pending):
        r = fut.result()
        if r is not None:
            results.append(r)
            if len(results) % 25 == 0 or r['result'] != 'success':
                print(json.dumps({'finished': len(results), 'target': len(groups), 'series_rows': sum(x.get('series_count', 0) for x in results), 'bytes': bytes_downloaded, 'last': r['group_code'], 'status': r['result'], 'elapsed_s': round(time.monotonic() - start, 1)}), flush=True)

summary = {'target_groups': len(groups), 'attempted_groups': len(results), 'successful_groups': sum(r['result'] == 'success' for r in results), 'failed_groups': [r for r in results if r['result'] != 'success'], 'unattempted_groups': sorted({g['group_code'] for g in groups} - {r['group_code'] for r in results}), 'series_catalog_rows': sum(r.get('series_count', 0) for r in results), 'bytes_downloaded': bytes_downloaded, 'elapsed_seconds': round(time.monotonic()-start, 1), 'stopped_by_budget_or_http': stop.is_set(), 'observations_downloaded': 0, 'scope': 'Public frontend category/group list and per-group series metadata only'}
summary['already_cached_groups'] = cached_count
summary['attempted_this_run'] = len(results) - cached_count
(ROOT / 'series_fetch_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k != 'unattempted_groups'}, ensure_ascii=False), flush=True)
