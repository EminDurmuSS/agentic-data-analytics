-- List locally queryable metrics.
SELECT source_system, metric_id, metric_name_tr, native_frequency, unit
FROM catalog.metrics
WHERE observation_available
ORDER BY source_system, metric_id;

-- Demo question: periods where the housing-credit flow rate decreased but
-- real housing-credit stock did not increase.
SELECT month,
       TP_KTF12 AS housing_credit_rate_pct,
       housing_rate_change_pp,
       housing_credit_real_mom_pct,
       source_scope_review_required
FROM analysis.housing_credit_monthly
WHERE rate_down_real_stock_not_up_quality_screened
ORDER BY month;

-- Compare quarterly stock scopes and true TBB disbursement flows.
SELECT quarter,
       bddk_housing_credit_stock_million_tl,
       finturk_domestic_provinces_million_try,
       balance_amount_million_try AS tbb_reporting_bank_stock_million_try,
       disbursement_amount_million_try AS tbb_disbursement_flow_million_try
FROM analysis.housing_credit_quarterly
ORDER BY quarter;
