"""Typed aggregation of imported event, static and calendar datasets."""
from __future__ import annotations

from decimal import Decimal, localcontext
import copy
import json
import math

import pandas as pd

from agentic_analytics.agent.schemas import STRING, obj
from agentic_analytics.lakehouse.service import LakehouseService, PlanError, _json, _name, error_envelope
from agentic_analytics.lakehouse.units import normalize_column


AGGREGATES = ('source_value', 'count', 'count_distinct', 'sum', 'mean', 'min', 'max', 'first', 'last', 'weighted_mean')


class DatasetTools:
    def __init__(self, store, workspace_id):
        self.store, self.workspace_id = store, workspace_id
        store.workspace(workspace_id)

    @staticmethod
    def plan(args):
        defaults = {'group_by': None, 'filters': None, 'time_bucket': None, 'aggregation_scope': 'within_series', 'scope_reason': None}
        return {'query_type': 'dataset', 'request': {**defaults, **copy.deepcopy(args)}}

    def recover(self, args, intent):
        workspace = self.store.workspace(self.workspace_id)
        if workspace['revision_id'] == intent.get('input_revision'):
            return None
        current = workspace.get('analysis_head')
        if current:
            frame, manifest = self.store.load_analysis(current)
            if (manifest.get('plan') == self.plan(args) and
                    manifest.get('workspace_revision_id') == intent.get('input_revision')):
                return LakehouseService._envelope(frame, manifest, [])
        return {'status': 'blocked', 'errors': [{'code': 'UNKNOWN_MUTATION_OUTCOME', 'message': 'Workspace changed after interrupted dataset query; refusing an uncertain replay.'}]}

    def aggregate_dataset(self, dataset_id, measures, *, group_by=None, filters=None, time_bucket=None,
                          aggregation_scope='within_series', scope_reason=None):
        args = {'dataset_id': dataset_id, 'measures': measures, 'group_by': group_by, 'filters': filters,
                'time_bucket': time_bucket, 'aggregation_scope': aggregation_scope, 'scope_reason': scope_reason}
        workspace = self.store.workspace(self.workspace_id)
        if dataset_id not in workspace.get('datasets', []):
            raise PlanError('Select an imported dataset in this workspace', code='DATASET_NOT_FOUND')
        manifest = self.store.dataset_manifest(dataset_id)
        contract = manifest['contract']
        columns = contract['columns']
        date_column = contract.get('date_column')
        frame = pd.read_parquet(self.store.overlay_path(dataset_id)).copy()
        if not 1 <= len(frame) <= 10000:
            raise PlanError('Dataset query supports 1 to 10000 input rows; select a bounded source', code='ROW_LIMIT')
        row_number_column, bucket_column = '__source_row_number', '__output_period'
        while row_number_column in frame:
            row_number_column += '_'
        while bucket_column in frame or bucket_column == row_number_column:
            bucket_column += '_'
        frame[row_number_column] = range(1, len(frame) + 1)
        if group_by is not None and (group_by not in columns or group_by in {'period', 'value', 'rank'}):
            categories = [name for name, meta in columns.items()
                          if name != date_column and name not in {'period', 'value', 'rank'}
                          and (meta.get('kind') == 'dimension' or meta.get('dtype') == 'string')]
            raise PlanError(
                'group_by identifies a category, not the date axis. Choose one existing category column '
                f'from {categories[:30]} or omit group_by. The source date column is {date_column!r}; '
                'for event records use time_bucket={"frequency":"daily"} to retain individual source dates. '
                'Keep line items as separate categories rather than combining them.', code='INVALID_DATASET_GROUP')
        if filters is None:
            filters = []
        if not isinstance(filters, list) or len(filters) > 12:
            raise PlanError('Use at most 12 typed column filters')
        fixed = set()
        for condition in filters:
            if (not isinstance(condition, dict) or set(condition) - {'column', 'op', 'value'} or
                    not {'column', 'op'} <= set(condition) or condition['column'] not in columns):
                raise PlanError('Filters require existing column, op and optional value')
            column, operation = condition['column'], condition['op']
            value = condition.get('value')
            if operation == 'is_null':
                if value is not None:
                    raise PlanError('is_null does not accept a value')
                selected = frame[column].isna()
            elif operation == 'in':
                if not isinstance(value, list) or not 1 <= len(value) <= 100 or any(isinstance(v, (list, dict)) for v in value):
                    raise PlanError('in requires 1 to 100 scalar values')
                selected = frame[column].isin(value)
            else:
                if operation not in {'eq', 'ne', 'gte', 'lte'} or isinstance(value, (list, dict)) or value is None:
                    raise PlanError('Use eq, ne, gte, lte, in or is_null with a valid value')
                try:
                    selected = {'eq': frame[column].eq, 'ne': frame[column].ne,
                                'gte': frame[column].ge, 'lte': frame[column].le}[operation](value)
                except (TypeError, ValueError) as exc:
                    raise PlanError('Filter value is incompatible with its column') from exc
                if operation == 'eq':
                    fixed.add(column)
            frame = frame.loc[selected.fillna(False)].copy()
        if frame.empty:
            raise PlanError('No source rows match the filters; no zero or fabricated observation was produced', code='MISSING_OBSERVATIONS')
        native_frequency = contract['frequency']
        frequency = native_frequency
        if time_bucket is not None:
            if not isinstance(time_bucket, dict) or set(time_bucket) != {'frequency'}:
                raise PlanError('time_bucket requires only a target frequency')
            frequency = time_bucket['frequency']
            allowed = {'daily': 'D', 'weekly': 'W-FRI', 'monthly': 'M', 'quarterly': 'Q', 'annual': 'Y'}
            ranks = {'event': 0, 'daily': 0, 'business_daily': 0, 'weekly': 1, 'monthly': 2, 'quarterly': 3, 'annual': 4}
            if not date_column or frequency not in allowed or native_frequency not in ranks or ranks[native_frequency] > ranks[frequency]:
                raise PlanError('Time bucketing needs a date axis and a native or coarser calendar; no upsampling')
            labels = frame[date_column].astype(str)
            source_aliases = {'monthly': 'M', 'quarterly': 'Q', 'annual': 'Y'}
            if native_frequency in source_aliases:
                times = pd.PeriodIndex(labels, freq=source_aliases[native_frequency]).to_timestamp()
            else:
                times = pd.to_datetime(labels, format='%Y-%m-%d', errors='raise')
            periods = pd.PeriodIndex(times, freq=allowed[frequency])
            frame[bucket_column] = periods.end_time.strftime('%Y-%m-%d') if frequency == 'weekly' else periods.astype(str)
        elif date_column:
            if native_frequency == 'event':
                raise PlanError('Event records need an explicit time_bucket before aggregation. '
                                'Use {"frequency":"daily"} to retain each source date; group_by is the separate category column.')
            frame[bucket_column] = frame[date_column].astype(str)
        else:
            frequency = 'static'
            frame[bucket_column] = 'Tüm kayıtlar'
        if not isinstance(measures, list) or not 1 <= len(measures) <= 6:
            raise PlanError('Choose 1 to 6 explicit measures')
        if aggregation_scope not in {'within_series', 'explicit_population'}:
            raise PlanError('Use within_series or explicit_population aggregation_scope')
        if aggregation_scope == 'explicit_population' and (not isinstance(scope_reason, str) or not 10 <= len(scope_reason.strip()) <= 500):
            raise PlanError('Population aggregation needs an explicit scope reason; overlapping totals cannot be assumed additive')
        if aggregation_scope == 'within_series' and scope_reason is not None:
            raise PlanError('scope_reason requires explicit_population')
        free_dimensions = set(contract['grain']) - {date_column, group_by} - fixed
        names, definitions = set(), {}
        for measure in measures:
            if (not isinstance(measure, dict) or set(measure) - {'name', 'op', 'column', 'weight'} or
                    not {'name', 'op'} <= set(measure)):
                raise PlanError('Measures require name, op, and source column except for row counts')
            name = _name(measure['name'])
            if name in names or name == group_by:
                raise PlanError('Output names must be distinct from each other and the group column')
            names.add(name)
            operation, column = measure['op'], measure.get('column')
            if operation not in AGGREGATES or (column not in columns and not (operation == 'count' and column is None)):
                raise PlanError('Select an existing source column and supported aggregation')
            if operation in {'count', 'count_distinct'}:
                meta = {'kind': ('count' if operation == 'count_distinct' else
                                 'count_flow' if frequency != 'static' else 'count_stock'),
                        'unit': 'count', 'scale': 1, 'status': 'ready',
                        'additive_over_time': operation != 'count_distinct'}
            else:
                try:
                    meta, _ = normalize_column(columns[column])
                except ValueError as exc:
                    raise PlanError(str(exc), code='SEMANTICS_REVIEW_REQUIRED') from exc
                if meta['dtype'] not in {'integer', 'float'} or meta['kind'] == 'dimension':
                    raise PlanError('Numeric aggregation needs a reviewed numeric measure', code='SEMANTICS_REVIEW_REQUIRED')
                from agentic_analytics.lakehouse.financial_semantics import cumulative_evidence
                cumulative = cumulative_evidence(meta)
                if operation == 'source_value':
                    # Reading one reported fact requires no assumption about
                    # additivity. It must never collapse records or change dates.
                    if free_dimensions or aggregation_scope != 'within_series':
                        raise PlanError('source_value preserves each source record. Group or filter all source dimensions; population reduction is not supported.', code='SCOPE_MISMATCH')
                    if date_column and (frequency != native_frequency and not
                                        (native_frequency == 'event' and frequency == 'daily')):
                        raise PlanError('source_value preserves native dates; select the source calendar without resampling.', code='INVALID_TEMPORAL_AGGREGATION')
                    if date_column and not frame[bucket_column].eq(frame[date_column].astype(str)).all():
                        raise PlanError('source_value cannot relabel source dates.', code='INVALID_TEMPORAL_AGGREGATION')
                    meta.update(additive_over_time=False, source_values_only=True)
                    if cumulative:
                        meta['cumulative_evidence'] = cumulative
                    meta['status'] = ('review_required' if cumulative or meta['kind'] == 'unknown'
                                      or meta.get('status') == 'review_required' else 'ready')
                elif meta['kind'] == 'unknown' or meta.get('status') == 'review_required':
                    raise PlanError('Numeric aggregation needs a reviewed numeric measure', code='SEMANTICS_REVIEW_REQUIRED')
                elif cumulative:
                    raise PlanError('Cumulative source values require an explicit reviewed conversion before numeric aggregation', code='CUMULATIVE_FLOW_REVIEW_REQUIRED')
                if free_dimensions and aggregation_scope != 'explicit_population':
                    raise PlanError('Unfixed row dimensions would be combined: ' + ', '.join(sorted(free_dimensions)) + '. Group/filter them or explicitly declare the population.', code='SCOPE_MISMATCH')
                if operation == 'sum' and meta['kind'] not in {'flow', 'count_flow', 'stock', 'count_stock'}:
                    raise PlanError('Only additive flows or same-date disjoint stock populations can be summed', code='INVALID_TEMPORAL_AGGREGATION')
                if operation == 'sum' and native_frequency == 'weekly' and frequency not in {'weekly', 'static'}:
                    raise PlanError('Weekly flows straddle calendar month/quarter boundaries; a justified allocation is required before summing', code='INVALID_TEMPORAL_AGGREGATION')
                if operation in {'mean', 'weighted_mean', 'min', 'max', 'first', 'last'}:
                    # A mean, distinct count or sampled extremum is not a whole
                    # period flow merely because its inputs were flow values.
                    meta['additive_over_time'] = False
                if operation != 'source_value':
                    meta.update(status='ready')
            if operation == 'weighted_mean':
                weight = measure.get('weight')
                if weight not in columns or columns[weight]['dtype'] not in {'integer', 'float'}:
                    raise PlanError('weighted_mean requires an explicit numeric weight column')
            elif 'weight' in measure:
                raise PlanError('Only weighted_mean accepts a weight column')
            definitions[name] = {**meta, 'aggregation': operation,
                'scope': {'dataset_id': dataset_id, 'group_by': group_by, 'filters': filters, 'aggregation_scope': aggregation_scope, 'scope_reason': scope_reason}}
        keys = ['period'] + ([group_by] if group_by else [])
        if group_by and frame[group_by].isna().any():
            raise PlanError('Group labels cannot be missing; review or explicitly filter the source')
        output, cells, warnings = [], {}, []
        for key, group in frame.groupby([bucket_column] + ([group_by] if group_by else []), sort=True, dropna=False):
            key = key if isinstance(key, tuple) else (key,)
            row = dict(zip(keys, _json(key)))
            refs = {}
            for measure in measures:
                name, operation, column = measure['name'], measure['op'], measure.get('column')
                values = group[column] if column else None
                missing = values is not None and values.isna().any()
                if operation in {'sum', 'mean', 'weighted_mean', 'first', 'last'} and time_bucket and native_frequency != 'event':
                    native_alias = {'daily': 'D', 'business_daily': 'B', 'weekly': 'W-FRI', 'monthly': 'M', 'quarterly': 'Q', 'annual': 'Y'}[native_frequency]
                    target_alias = {'daily': 'D', 'weekly': 'W-FRI', 'monthly': 'M', 'quarterly': 'Q', 'annual': 'Y'}[frequency]
                    target_period = pd.Period(row['period'], freq=target_alias)
                    expected = set(pd.period_range(target_period.start_time, target_period.end_time, freq=native_alias))
                    series_dimensions = [name for name in contract['grain'] if name != date_column]
                    series_parts = (part for _, part in group.groupby(series_dimensions, sort=False, dropna=False)) if series_dimensions else [group]
                    for series_part in series_parts:
                        observed = set(pd.PeriodIndex(series_part[date_column].astype(str), freq=native_alias))
                        if operation in {'first', 'last'}:
                            endpoint = min(expected) if operation == 'first' else max(expected)
                            if endpoint not in observed:
                                missing = True
                        elif expected != observed:
                            missing = True
                if operation == 'source_value':
                    if len(group) != 1:
                        raise PlanError('source_value needs exactly one original record per output key; duplicate or distinct-period records cannot be selected by order.', code='SOURCE_VALUE_NOT_UNIQUE')
                    value = None if missing else values.iloc[0]
                elif operation == 'count':
                    value = len(group) if values is None else int(values.notna().sum())
                elif operation == 'count_distinct':
                    value = int(values.nunique(dropna=True))
                elif operation == 'sum' and definitions[name]['kind'] in {'stock', 'count_stock'}:
                    if date_column and group[date_column].nunique() != 1:
                        raise PlanError('Stock observations from different dates cannot be summed', code='INVALID_TEMPORAL_AGGREGATION')
                    if len(group) > 1 and aggregation_scope != 'explicit_population':
                        raise PlanError('Stock totals need an explicit disjoint population scope', code='SCOPE_MISMATCH')
                    value = None if missing else sum((Decimal(str(v)) for v in values), Decimal(0))
                elif missing:
                    value = None
                elif operation in {'first', 'last'}:
                    if not date_column or group[date_column].duplicated().any():
                        raise PlanError('first/last require unique source dates within each group; source file order is not a time order')
                    ordered = group.sort_values(date_column, kind='stable')
                    value = ordered[column].iloc[0 if operation == 'first' else -1]
                elif operation in {'min', 'max'}:
                    value = getattr(values, operation)()
                elif operation in {'sum', 'mean'}:
                    value = sum((Decimal(str(v)) for v in values), Decimal(0))
                    if operation == 'mean':
                        value /= len(values)
                elif operation == 'weighted_mean':
                    weights = group[measure['weight']]
                    if weights.isna().any():
                        value, missing = None, True
                    else:
                        with localcontext() as context:
                            context.prec = 80
                            pairs = [(Decimal(str(v)), Decimal(str(w))) for v, w in zip(values, weights)]
                            total_weight = sum((w for _, w in pairs), Decimal(0))
                            if any(w < 0 for _, w in pairs) or total_weight <= 0:
                                raise PlanError('Weights must be nonnegative with a positive total')
                            value = sum((v*w for v,w in pairs), Decimal(0)) / total_weight
                if value is not None:
                    value = Decimal(str(value))
                    if not value.is_finite():
                        raise PlanError('Aggregate is not finite')
                    value = int(value) if value == value.to_integral_value() else float(value)
                    if isinstance(value, float) and not math.isfinite(value):
                        raise PlanError('Aggregate exceeds the supported numeric range')
                row[name] = value
                refs[name] = {'source_column': column, 'operation': operation, 'weight_column': measure.get('weight'),
                              'source_rows': group[row_number_column].tolist(), 'source_values': _json(values.tolist()) if values is not None else None,
                              'source_weights': _json(group[measure['weight']].tolist()) if operation == 'weighted_mean' else None}
                if missing:
                    warnings.append({'code': 'MISSING_AGGREGATE_INPUT', 'column': name, 'period': row['period'], 'message': 'Source nulls were preserved; no partial numeric total was substituted.'})
            output.append(row)
            cells[json.dumps([row[k] for k in keys], ensure_ascii=False)] = refs
        if len(output) > 10000:
            raise PlanError('Aggregation output exceeds 10000 rows')
        result = pd.DataFrame(output, dtype=object)
        for name in names:
            values = [row[name] for row in output]
            observed = [value for value in values if value is not None]
            if all(isinstance(value, int) for value in observed):
                if any(not -(2**63) <= value < 2**63 for value in observed):
                    raise PlanError('Integer aggregation exceeds the supported Int64 range', code='NUMERIC_PRECISION_UNSUPPORTED')
                result[name] = pd.Series(values, dtype='Int64')
            else:
                if any(isinstance(value, int) and abs(value) > 2**53 for value in observed):
                    raise PlanError('Mixed fractional and large integer results cannot be represented exactly in one float column', code='NUMERIC_PRECISION_UNSUPPORTED')
                result[name] = pd.Series(values, dtype='Float64')
        if group_by:
            definitions[group_by] = {'kind': 'dimension', 'unit': 'label', 'status': 'ready'}
        warnings.append({'code': 'OBSERVED_GROUPS_ONLY', 'message': 'Only observed groups and periods are shown. Missing event periods are not asserted to have zero activity.'})
        lineage = {'frequency': frequency, 'dataset_query': {'dataset_id': dataset_id, 'source_sha256': manifest['source_sha256'],
                   'document_provenance': contract.get('document_provenance'), 'row_index_basis': 'one-based data row in immutable dataset',
                   'input_keys': contract['key'], 'output_keys': keys, 'cells': cells}, 'warnings': warnings,
                   'join_contract': {'key': keys, 'cardinality': 'one_row_per_output_key', 'population_equivalence_asserted': False}}
        if group_by:
            lineage['group_by'] = group_by
        saved = self.store.save_analysis(self.workspace_id, result, self.plan(args), lineage, schema=definitions, expected_version=workspace['version'])
        return LakehouseService._envelope(result, saved, warnings)

    def extra_tools(self):
        def run(args):
            try:
                return self.aggregate_dataset(**args)
            except (ValueError, OSError) as exc:
                return error_envelope(exc)
        scalar = {'type': ['string', 'number', 'boolean', 'null']}
        params = obj({'dataset_id': STRING,
            'measures': {'type': 'array', 'minItems': 1, 'maxItems': 6, 'items': obj({'name': STRING, 'op': {'enum': list(AGGREGATES)}, 'column': STRING, 'weight': STRING}, ['name', 'op'])},
            'group_by': {'type': 'string', 'description': 'One existing category column such as line_item or entity. Do not use the date column or reserved output names period/value/rank. Omit for an ungrouped result.'},
            'filters': {'type': 'array', 'maxItems': 12, 'items': obj({'column': STRING, 'op': {'enum': ['eq','ne','gte','lte','in','is_null']}, 'value': {'anyOf': [scalar, {'type': 'array', 'items': scalar, 'maxItems': 100}]}}, ['column','op'])},
            'time_bucket': obj({'frequency': {'enum': ['daily','weekly','monthly','quarterly','annual']}}),
            'aggregation_scope': {'enum': ['within_series','explicit_population']}, 'scope_reason': STRING}, ['dataset_id','measures'])
        return {'aggregate_dataset': {'schema': {'type': 'function', 'function': {'name': 'aggregate_dataset',
            'description': 'Analyze an imported event/static/calendar table with explicit filters, category group and numeric measures. source_value copies exactly one original numeric record per output key, preserving native dates and semantic uncertainty; use it to display reported facts without aggregation or invented stock/flow assumptions. For line-item observations, group_by is the actual line-item column; the date axis comes from the source contract. Event rows require time_bucket={frequency:daily} to retain their individual dates. Static tables need no invented date or time_bucket. count without column counts source records. Cross-entity sums need explicit_population and a factual scope_reason; never sum stocks across dates. Output is a saved analysis usable by charts and summaries, with source-row lineage.',
            'parameters': params}}, 'handler': run, 'mutating': True, 'recover': self.recover}}
