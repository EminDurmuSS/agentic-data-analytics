"""Independent local validation against separately published official CPI HTML."""
import hashlib
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[1] / 'raw'

def series(name, key):
    return {row['Tarih']: float(row[key]) for row in json.loads((DATA / name).read_text())['items']}

def main():
    html = (HERE / 'cpi_official_changes.html').read_text()
    published = {}
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = [re.sub('<[^>]+>', '', cell).strip() for cell in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if len(cells) == 3 and re.fullmatch(r'\d{2}-\d{4}', cells[0]):
            published[cells[0][3:] + '-' + cells[0][:2]] = {'yoy': float(cells[1]), 'mom': float(cells[2])}
    cpi_filename = 'core_cpi.json' if (DATA / 'core_cpi.json').exists() else 'cpi_data.json'
    cpi = series(cpi_filename, 'TP_TUKFIY2025_GENEL')
    kfe = series('core_kfe_tr.json', 'TP_KFE_TR')
    checks = []
    for month in sorted(cpi):
        if not '2021-01' <= month <= '2026-06':
            continue
        year, num = map(int, month.split('-'))
        previous = f'{year-1}-12' if num == 1 else f'{year}-{num-1:02}'
        for change, denominator in [('mom', previous), ('yoy', f'{year-1}-{num:02}')]:
            if denominator in cpi and month in published:
                calculated = 100 * (cpi[month] / cpi[denominator] - 1)
                expected = published[month][change]
                checks.append({'month': month, 'change': change, 'calculated': calculated, 'published': expected,
                               'match_2dp': round(calculated, 2) == expected, 'error_pp': calculated-expected})
    (HERE / 'cpi_change_checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    summary = {
        'audited_at_utc': datetime.now(timezone.utc).isoformat(),
        'target_period': ['2021-01', '2026-06'],
        'cpi_input': cpi_filename,
        'cpi_input_sha256': hashlib.sha256((DATA / cpi_filename).read_bytes()).hexdigest(),
        'cpi_checks': {'count': len(checks), 'matched_2dp': sum(x['match_2dp'] for x in checks),
                       'mom_count': sum(x['change']=='mom' for x in checks), 'yoy_count': sum(x['change']=='yoy' for x in checks),
                       'mismatches': [x for x in checks if not x['match_2dp']],
                       'meaning': 'Growth rates independently matched against TCMB HTML publication of TÜİK rates; not an independent level or primary collection audit.'},
        'base_means': {
            'cpi_2025': {'count': sum(m.startswith('2025-') for m in cpi), 'mean': statistics.mean(v for m,v in cpi.items() if m.startswith('2025-')), 'expected':100, 'tolerance':0.000001},
            'kfe_2023': {'count': sum(m.startswith('2023-') for m in kfe), 'mean': statistics.mean(v for m,v in kfe.items() if m.startswith('2023-')), 'expected':100, 'tolerance':0.005},
        },
        'spot_levels_evds_only': {m:{'cpi':cpi[m],'kfe':kfe[m]} for m in ['2021-01','2023-12','2026-06']},
        'not_independently_matched': ['KFE historical exact levels outside EVDS', 'Housing interest historical exact rates outside EVDS', 'CPI absolute levels in a second publication; CPI growth rates are matched'],
        'methodology_dates': {'kfe_metadata':'2026-02-17','housing_rate_metadata':'2026-04-27','cpi_rebase_announcement':'2025-10-30'},
        'important_interpretation': [
            'TP.KTF12 is annual effective TRY fixed-rate housing-loan interest, published weekly, flow weighted.',
            'Monthly simple mean of published weekly annual rates is a derived summary, not a monthly volume-weighted rate.',
            'Rate bank coverage excludes participation banks; do not silently equate to BDDK all-bank loan stock.',
            'KFE current base2023 uses latest revised history and is not a sale-price level or all-transactions average.',
            'CPI current base2025 series includes back-history; never append2003baselevels directly.',
            'Historical observations are current-vintage revised data, not proof of what was publicly known on historical date.',
        ],
    }
    assert len(checks) == 132 and all(x['match_2dp'] for x in checks), 'Independent CPI publication mismatch'
    for check in summary['base_means'].values():
        assert abs(check['mean']-check['expected']) < check['tolerance']
    (HERE.parent / 'metadata_audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
