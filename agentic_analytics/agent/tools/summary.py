"""Reproducible period summaries over full, immutable analysis results."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

import pandas as pd

from agentic_analytics.agent.schemas import STRING, obj
from agentic_analytics.lakehouse.service import PlanError, _json, _period, error_envelope


STATISTICS = ('first', 'last', 'min', 'max', 'sum', 'mean', 'count', 'change', 'growth')
GROWTH_KINDS = {'stock', 'flow', 'count_stock', 'count_flow', 'count', 'price'}
LABELS = {'first': 'İlk değer', 'last': 'Son değer', 'min': 'En düşük', 'max': 'En yüksek',
          'sum': 'Dönem toplamı', 'mean': 'Gözlemlerin aritmetik ortalaması', 'count': 'Dolu gözlem sayısı',
          'change': 'İlk-son farkı', 'growth': 'İlk-son yüzde değişimi',
          'window_difference': 'Dönemler arası fark', 'window_growth': 'Dönemler arası yüzde değişimi'}


def format_number(value):
    if value is None:
        return 'hesaplanamadı'
    if isinstance(value, int):
        return f'{value:,}'.replace(',', '.')
    if 0 < abs(value) < 0.000001:
        return f'{value:.6g}'.replace('.', ',')
    return f'{value:,.6f}'.rstrip('0').rstrip('.').replace(',', '_').replace('.', ',').replace('_', '.')


def unit_label(unit, scale=1):
    if unit in {'TRY', 'USD', 'EUR', 'GBP'}:
        prefix = {1: '', 1000: 'bin ', 1000000: 'milyon ', 1000000000: 'milyar '}.get(scale, f'{scale:g} × ')
        return prefix + {'TRY': 'TL', 'USD': 'USD', 'EUR': 'EUR', 'GBP': 'GBP'}[unit]
    return {'percent': '%', 'percentage_point': 'yüzde puan', 'person': 'kişi', 'persons': 'kişi',
            'index': 'endeks', 'observations': 'gözlem', 'count': 'adet'}.get(unit, unit or 'birim belirtilmemiş')


def _number(value):
    if value is None or pd.isna(value):
        return None
    value = Decimal(str(value))
    if not value.is_finite():
        raise PlanError('Summary cannot contain nonfinite values', code='INVALID_SUMMARY_VALUE')
    if value == value.to_integral_value():
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise PlanError('Summary exceeds the supported numeric range', code='NUMERIC_PRECISION_UNSUPPORTED')
    return result


class SummaryTools:
    def __init__(self, store, workspace_id):
        self.store, self.workspace_id = store, workspace_id
        store.workspace(workspace_id)
        self.root = store.root / 'summaries' / workspace_id
        self.root.mkdir(parents=True, exist_ok=True)

    def load_artifact(self, artifact_id):
        if not isinstance(artifact_id, str) or not re.fullmatch(r'summary_[a-f0-9]{64}', artifact_id):
            raise PlanError('Invalid summary identifier')
        encoded = (self.root / (artifact_id + '.json')).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != artifact_id.removeprefix('summary_'):
            raise PlanError('Summary hash mismatch', code='SUMMARY_INTEGRITY_ERROR')
        result = json.loads(encoded)
        _, manifest = self.store.load_analysis(result['analysis_id'])
        if (manifest['workspace_id'] != self.workspace_id or
                manifest['data_sha256'] != result['provenance']['data_sha256']):
            raise PlanError('Summary source does not match its workspace', code='SUMMARY_INTEGRITY_ERROR')
        return {**result, 'summary_id': artifact_id, 'artifact_id': artifact_id, 'artifact_ref': artifact_id}

    def summarize_analysis(self, analysis_id, columns=None, windows=None, statistics=None, compare_windows=False):
        frame, manifest = self.store.load_analysis(analysis_id)
        if manifest['workspace_id'] != self.workspace_id:
            raise PlanError('Analysis belongs to another workspace')
        if not 1 <= len(frame) <= 10000 or 'period' not in frame:
            raise PlanError('Summary requires a saved analysis of 1 to 10000 rows')
        group_by = manifest.get('lineage', {}).get('group_by')
        schema = manifest.get('schema', {})
        available = [name for name in frame if name not in {'period', group_by, 'rank'}
                     and pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name])]
        columns = available[:6] if columns is None else columns
        if (not isinstance(columns, list) or not 1 <= len(columns) <= 6 or len(set(columns)) != len(columns)
                or any(name not in available for name in columns)):
            raise PlanError('Select 1 to 6 distinct saved numeric columns')
        if statistics is not None and (not isinstance(statistics, list) or not statistics or
                len(statistics) > len(STATISTICS) or len(set(statistics)) != len(statistics) or any(s not in STATISTICS for s in statistics)):
            raise PlanError('Choose supported distinct summary statistics')
        if type(compare_windows) is not bool:
            raise PlanError('compare_windows must be a boolean')
        frequency = manifest.get('plan', {}).get('frequency') or manifest.get('lineage', {}).get('frequency')
        labels = frame['period'].astype(str)
        if windows is None:
            windows = [{'label': f'{labels.min()} - {labels.max()}', 'start': labels.min(), 'end': labels.max()}]
        if not isinstance(windows, list) or not 1 <= len(windows) <= 4:
            raise PlanError('Select 1 to 4 explicit summary windows')
        if compare_windows and len(windows) != 2:
            raise PlanError('Comparisons require exactly two windows, baseline first and current second')
        if group_by and (group_by not in frame or frame.duplicated(['period', group_by]).any()):
            raise PlanError('Summary requires one value per group and period')
        if not group_by and frame['period'].duplicated().any():
            raise PlanError('Select a unique time axis for summary')
        groups = list(frame.groupby(group_by, sort=False, dropna=False)) if group_by else [(None, frame)]
        if len(groups) > 100:
            raise PlanError('Summary exceeds 100 groups; narrow the saved analysis')
        facts, warnings, names = [], [], set()

        def fact(column, window, statistic, value, meta, group, **extra):
            payload = {'source_analysis_id': analysis_id, 'column': column, 'window': window['label'],
                       'period_start': window['start'], 'period_end': window['end'], 'statistic': statistic,
                       'value': _number(value), 'unit': meta.get('unit'), 'scale': meta.get('scale', 1),
                       'dimensions': {group_by: _json(group)} if group_by else {}, **extra}
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
            payload['fact_id'] = 'fact_' + hashlib.sha256(encoded).hexdigest()[:24]
            facts.append(payload)
            return payload

        for window in windows:
            if (not isinstance(window, dict) or set(window) != {'label', 'start', 'end'}
                    or any(not isinstance(window[k], str) or not window[k].strip() for k in window)
                    or len(window['label']) > 100 or window['label'] in names):
                raise PlanError('Windows require unique short labels and explicit start/end periods')
            names.add(window['label'])
            if frequency != 'static':
                first, last = _period(window['start'], frequency), _period(window['end'], frequency)
                if first > last:
                    raise PlanError('Window start must not follow end')
                expected_count = len(pd.period_range(first, last)) if frequency not in {'half_yearly', 'twice_monthly'} else None
            elif window['start'] != window['end']:
                raise PlanError('Static snapshots have one explicit snapshot label')
            else:
                expected_count = 1
            if window['start'] < labels.min() or window['end'] > labels.max():
                raise PlanError('Summary window exceeds the saved analysis; extend it before calculating', code='SUMMARY_WINDOW_OUT_OF_RANGE')
            for group, group_frame in groups:
                part = group_frame.loc[(group_frame['period'].astype(str) >= window['start']) &
                                       (group_frame['period'].astype(str) <= window['end'])].sort_values('period', kind='stable')
                for column in columns:
                    meta = schema.get(column, {})
                    kind = meta.get('kind', 'unknown')
                    ready = meta.get('status') == 'ready'
                    selected = statistics or ['first', 'last', 'min', 'max', 'count']
                    if statistics is None and ready and kind in GROWTH_KINDS:
                        selected += ['change', 'growth']
                    for statistic in selected:
                        if statistic == 'sum' and (not ready or kind not in {'flow', 'count_flow'} or meta.get('additive_over_time') is False):
                            raise PlanError('Period sums require reviewed noncumulative flows; stocks and unknown measures cannot be summed', code='INVALID_TEMPORAL_AGGREGATION')
                        if statistic in {'mean', 'change', 'growth'} and not ready:
                            raise PlanError('Calculated summaries require reviewed semantics', code='SEMANTICS_REVIEW_REQUIRED')
                        if statistic == 'growth' and kind not in GROWTH_KINDS:
                            raise PlanError('Use differences for rates, ratios and indices, not percentage growth', code='INVALID_SUMMARY_STATISTIC')
                        values = part[column]
                        complete = len(values) > 0 and not values.isna().any()
                        if expected_count is not None and len(values) != expected_count:
                            complete = False
                        # Top-N grouped snapshots can omit a group in some periods. Do not
                        # turn a partial ranking into a complete-period sum or mean.
                        expected_labels = set(labels[(labels >= window['start']) & (labels <= window['end'])])
                        if set(part['period'].astype(str)) != expected_labels:
                            complete = False
                        value = None
                        if statistic == 'count':
                            value = int(values.notna().sum())
                        elif statistic in {'first', 'last'} and len(values):
                            position = 0 if statistic == 'first' else -1
                            boundary = window['start'] if statistic == 'first' else window['end']
                            if str(part.iloc[position]['period']) == boundary:
                                value = values.iloc[position]
                        elif statistic in {'min', 'max'} and values.notna().any():
                            value = getattr(values, statistic)()
                        elif complete:
                            decimals = [Decimal(str(v)) for v in values]
                            if statistic == 'sum':
                                value = sum(decimals, Decimal(0))
                            elif statistic == 'mean':
                                value = sum(decimals, Decimal(0)) / len(decimals)
                            elif statistic == 'change':
                                value = decimals[-1] - decimals[0]
                            elif statistic == 'growth' and decimals[0] > 0:
                                value = (decimals[-1] / decimals[0] - 1) * 100
                        result_meta = dict(meta)
                        if statistic == 'count':
                            result_meta.update(unit='observations', scale=1)
                        elif statistic == 'growth':
                            result_meta.update(unit='percent', scale=1)
                        elif statistic == 'change' and meta.get('unit') in {'percent', '%'}:
                            result_meta.update(unit='percentage_point', scale=1)
                        fact(column, window, statistic, value, result_meta, group,
                             observed_count=int(values.notna().sum()), row_count=len(values), complete=complete)
                        if not complete:
                            warning = {'code': 'INCOMPLETE_SUMMARY_WINDOW', 'column': column, 'window': window['label'],
                                       'message': 'Missing rows or nulls remain explicit; complete-period calculations are unavailable.'}
                            if warning not in warnings:
                                warnings.append(warning)
        if len(facts) > 2000:
            raise PlanError('Summary exceeds 2000 facts; select fewer groups, columns or statistics')
        if compare_windows:
            base_facts = list(facts)
            for current in base_facts:
                if current['window'] != windows[1]['label'] or current['statistic'] not in {'sum', 'mean', 'first', 'last'}:
                    continue
                baseline = next((f for f in base_facts if f['window'] == windows[0]['label'] and
                                 f['column'] == current['column'] and f['statistic'] == current['statistic'] and f['dimensions'] == current['dimensions']), None)
                if not baseline:
                    continue
                meta = schema[current['column']]
                if meta.get('status') != 'ready':
                    raise PlanError('Window differences require reviewed semantics', code='SEMANTICS_REVIEW_REQUIRED')
                a, b = baseline['value'], current['value']
                delta = Decimal(str(b)) - Decimal(str(a)) if a is not None and b is not None else None
                compared = {'label': windows[0]['label'] + ' → ' + windows[1]['label'], 'start': windows[0]['start'], 'end': windows[1]['end']}
                group = next(iter(current['dimensions'].values()), None)
                refs = {'input_fact_ids': [baseline['fact_id'], current['fact_id']], 'basis_statistic': current['statistic']}
                difference_meta = {**meta, 'unit': 'percentage_point', 'scale': 1} if meta.get('unit') in {'percent', '%'} else meta
                fact(current['column'], compared, 'window_difference', delta, difference_meta, group, **refs)
                if meta.get('status') == 'ready' and meta.get('kind') in GROWTH_KINDS:
                    growth = delta / Decimal(str(a)) * 100 if delta is not None and a > 0 else None
                    fact(current['column'], compared, 'window_growth', growth, {'unit': 'percent', 'scale': 1}, group, **refs)
        if len(facts) > 2000:
            raise PlanError('Summary including comparisons exceeds 2000 facts; narrow the requested scope')
        lines = []
        for item in facts[:80]:
            group_label = ', '.join(str(v) for v in item['dimensions'].values())
            suffix = f' ({group_label})' if group_label else ''
            lines.append(f"{item['window']} | {item['column']}{suffix} | {LABELS[item['statistic']]}: {format_number(item['value'])} {unit_label(item['unit'], item['scale'])}")
        if len(facts) > 80:
            lines.append(f'İlk 80 bulgu gösterildi; {len(facts)} bulgunun tamamı kayıtlı özet dosyasında.')
        payload = {'status': 'ok', 'analysis_id': analysis_id, 'workspace_id': self.workspace_id,
                   'provenance': {'data_sha256': manifest['data_sha256'], 'snapshot_id': manifest['snapshot_id'], 'lineage_ref': analysis_id},
                   'parameters': {'columns': columns, 'windows': windows, 'statistics': statistics, 'compare_windows': compare_windows},
                   'facts': facts, 'summary_text': '\n'.join(lines), 'warnings': warnings}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        identifier = 'summary_' + hashlib.sha256(encoded).hexdigest()
        target = self.root / (identifier + '.json')
        if not target.exists():
            fd, name = tempfile.mkstemp(dir=self.root, suffix='.tmp')
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        return {**payload, 'summary_id': identifier, 'artifact_id': identifier, 'artifact_ref': identifier,
                'facts': facts[:80], 'fact_count': len(facts), 'preview_truncated': len(facts) > 80}

    def extra_tools(self):
        def run(args):
            try:
                return self.summarize_analysis(**args)
            except (ValueError, OSError) as exc:
                return error_envelope(exc)
        parameters = obj({'analysis_id': STRING, 'columns': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': STRING},
                          'windows': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': obj({'label': STRING, 'start': STRING, 'end': STRING})},
                          'statistics': {'type': 'array', 'minItems': 1, 'maxItems': len(STATISTICS), 'items': {'enum': list(STATISTICS)}},
                          'compare_windows': {'type': 'boolean'}}, ['analysis_id'])
        return {'summarize_analysis': {'schema': {'type': 'function', 'function': {'name': 'summarize_analysis',
                'description': 'Compute verified numeric facts from the full saved analysis. Use explicit windows and sum for cumulative period profits from monthly flows; compare_windows=true compares exactly two windows, baseline first. Never sum stocks. Default returns first/last/min/max/count and allowed changes. Final answers use these facts, not model arithmetic.',
                'parameters': parameters}}, 'handler': run}}
