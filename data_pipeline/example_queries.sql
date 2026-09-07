-- Preserves all 66 rows and existing columns; no stock sums over time.
SELECT month, housing_credit_three_groups_stock_million_tl,
       housing_rate_annual_pct_weekly_mean, housing_credit_real_jan2021_million_tl,
       kfe_tr_index2023, mortgage_sales_share_pct
FROM monthly_analytics ORDER BY month;

-- Descriptive co-occurrence, NOT a causal estimate or new credit flow.
SELECT month, housing_rate_change_pp, housing_credit_real_mom_pct,
       housing_sales_mortgaged_yoy_pct
FROM monthly_analytics
WHERE rate_down_real_stock_not_up = 1 ORDER BY month;

-- Web evidence annotations stay separate; exact effective dates may be unknown.
SELECT month, housing_rate_change_pp, housing_credit_real_mom_pct, context_event_ids
FROM monthly_with_context WHERE context_event_ids IS NOT NULL ORDER BY month;
