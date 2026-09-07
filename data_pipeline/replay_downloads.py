"""Replay the exact successfully observed anonymous public EVDS queries.

Run: python replay_downloads.py --destination new_snapshot
Existing frozen raw files are never overwritten. Public frontend routes can change.
Refresh the registry/checksums deliberately before accepting a new vintage.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parent
URL='https://evds3.tcmb.gov.tr/igmevdsms-dis/fe'

def replay(destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    registry=json.loads((ROOT/'series_registry.json').read_text())
    jobs={r['request_file']:r['raw_file'] for r in registry}
    results=[]
    for request_file,response_file in jobs.items():
        body=(ROOT/'raw'/request_file).read_bytes()
        request=urllib.request.Request(URL,data=body,method='POST',headers={
            'Content-Type':'application/json','Accept':'application/json',
            'Origin':'https://evds3.tcmb.gov.tr','Referer':'https://evds3.tcmb.gov.tr/',
            'User-Agent':'Mozilla/5.0'})
        record=dict(url=URL,method='POST',request_file=request_file,response_file=response_file,
            retrieved_at_utc=datetime.now(timezone.utc).isoformat())
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                content=response.read();record['http_status']=response.status
            parsed=json.loads(content)
            if not isinstance(parsed.get('items'),list) or not parsed['items']:
                raise ValueError('No observation list returned')
            (destination/response_file).write_bytes(content)
            (destination/request_file).write_bytes(body)
            record.update(status='downloaded',rows=len(parsed['items']),sha256=hashlib.sha256(content).hexdigest())
        except Exception as exc:
            record.update(status='failed',error=str(exc))
            if isinstance(exc,urllib.error.HTTPError):record['http_status']=exc.code
        results.append(record)
        (destination/'download_manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(record,ensure_ascii=False),flush=True)
        if record.get('http_status') in [401,403,429]:
            raise RuntimeError('Access/rate-limit response; stopped without retry or bypass.')
        time.sleep(0.2)
    if any(r['status']=='failed' for r in results):
        raise RuntimeError('One or more downloads failed; see manifest.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True)
    replay(parser.parse_args().destination)
