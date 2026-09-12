import copy

import pandas as pd
import pytest

from agentic_analytics.lakehouse.presentation import analysis_presentation


def test_default_view_uses_common_scale_without_changing_cells_or_hiding_independent_outputs():
    frame = pd.DataFrame({"period":["2026-03"],"firm_assets":[4783750292],"sector_assets":[49735194],
        "firm_million":[4783750.292],"firm_billion":[4783.750292],"ratio_pct":[9.618441],"delta":[25]})
    stock = {"kind":"stock","unit":"TRY","currency":"TRY","scale":1000}
    manifest = {"schema":{"firm_assets":stock,"firm_million":{**stock,"scale":1000000},
        "firm_billion":{**stock,"scale":1000000000},"sector_assets":{**stock,"scale":1000000},
        "ratio_pct":{"kind":"ratio","unit":"percent","scale":1},"delta":stock},
        "lineage":{"operations":[{"op":"scale","column":"firm_assets","output":"firm_million","target_scale":1000000},
            {"op":"scale","column":"firm_assets","output":"firm_billion","target_scale":1000000000},
            {"op":"ratio","column":"firm_million","denominator":"sector_assets","output":"ratio_pct"},
            {"op":"difference","column":"firm_assets","output":"delta"}],
            "sources":{"firm_assets":{"binding":{"title":"source_financial_facts: amount","source_system":"SESSION_DATASET"}},
                         "sector_assets":{"binding":{"title":"TOPLAM AKTİFLER [Toplam]","source_system":"CATALOG_MONTHLY",
                             "dimension_labels":{"group":{"all":"Sektör"}}},"dimensions":{"group":"all"}}}}}
    before, original = frame.copy(deep=True), copy.deepcopy(manifest)
    view = analysis_presentation(frame,manifest)
    assert view["columns"]==["period","firm_million","sector_assets","ratio_pct","delta"]
    assert view["labels"]["firm_million"]==view["labels"]["firm_assets"]=="Firm assets"
    assert view["labels"]["sector_assets"]=="CATALOG toplam aktifler (Sektör)"
    assert view["labels"]["ratio_pct"]=="Ratio"
    assert "source_financial_facts" not in str(view)
    pd.testing.assert_frame_equal(frame,before)
    assert manifest==original


def test_same_metric_different_line_items_and_group_dimensions_remain_distinct():
    frame=pd.DataFrame({"period":["2026-03"],"bank":["A"],"cash":[10],"assets":[100]})
    metadata={"metric_id":"overlay:dataset:amount","kind":"stock","unit":"TRY","currency":"TRY","scale":1000}
    manifest={"schema":{"cash":metadata,"assets":metadata},"lineage":{"sources":{
        "cash":{"binding":{"title":"source_financial_facts: amount","source_system":"SESSION_DATASET"},"dimensions":{"line_item":"Cash"}},
        "assets":{"binding":{"title":"source_financial_facts: amount","source_system":"SESSION_DATASET"},"dimensions":{"line_item":"Total assets"}}}}}
    view=analysis_presentation(frame,manifest)
    assert view["columns"]==list(frame.columns)
    assert view["labels"]["cash"]=="Cash" and view["labels"]["assets"]=="Assets"


def test_native_grouped_fact_table_keeps_period_and_line_items():
    frame=pd.DataFrame({"period":["2025-12-31","2026-03-31"],"line_item":["Cash","Total assets"],"amount":[20,100]})
    manifest={"schema":{"amount":{"kind":"unknown","unit":"TRY","currency":"TRY","scale":1000}}}
    view=analysis_presentation(frame,manifest)
    assert view["columns"]==list(frame.columns)
    assert view["labels"]=={"period":"Dönem","line_item":"Kalem","amount":"Tutar"}


@pytest.mark.parametrize("overwrite_original", [False, True])
def test_in_place_growth_is_not_hidden_with_a_prior_scale_family(overwrite_original):
    stock={"kind":"stock","unit":"TRY","currency":"TRY","scale":1000}
    growth={"kind":"ratio","unit":"percent","scale":1}
    target="assets" if overwrite_original else "growth_pct"
    scaled="million" if overwrite_original else "growth_pct"
    frame=pd.DataFrame({"period":["2026-01","2026-02"],"assets":[100000,110000],scaled:[100,110]})
    frame[target]=[None,10.0]
    schema={"assets":stock,scaled:{**stock,"scale":1000000},target:growth}
    manifest={"schema":schema,"lineage":{"operations":[
        {"op":"scale","column":"assets","output":scaled,"target_scale":1000000},
        {"op":"growth","column":target,"output":target,"periods":1}],
        "sources":{"assets":{"binding":{"title":"Assets","source_system":"TEST"}}}}}
    before=frame.copy(deep=True)
    view=analysis_presentation(frame,manifest)
    assert set(view["columns"])==set(frame.columns)
    assert "büyüme" in view["labels"][target]
    untransformed=scaled if overwrite_original else "assets"
    assert "büyüme" not in view["labels"][untransformed]
    pd.testing.assert_frame_equal(frame,before)


def test_non_scale_overwrite_starts_new_family_and_later_scale_inherits_only_that_version():
    stock={"kind":"stock","unit":"TRY","currency":"TRY","scale":1000}
    frame=pd.DataFrame({"period":["2026-01","2026-02"],"assets":[100000,110000],
                        "million":[None,10.0],"delta_scaled":[None,10000.0]})
    manifest={"schema":{"assets":stock,"million":{**stock,"scale":1000000},"delta_scaled":stock},
        "lineage":{"operations":[
            {"op":"scale","column":"assets","output":"million","target_scale":1000000},
            {"op":"difference","column":"million","output":"million"},
            {"op":"scale","column":"million","output":"delta_scaled","target_scale":1000}]}}
    view=analysis_presentation(frame,manifest)
    assert view["columns"]==["period","assets","delta_scaled"]
    assert "değişim" in view["labels"]["delta_scaled"] and "değişim" not in view["labels"]["assets"]


def test_in_place_scale_still_represents_the_same_quantity():
    stock={"kind":"stock","unit":"TRY","currency":"TRY","scale":1000000}
    frame=pd.DataFrame({"period":["2026-03"],"assets":[0.11],"million":[110.0]})
    manifest={"schema":{"assets":{**stock,"scale":1000000000},"million":stock},"lineage":{"operations":[
        {"op":"scale","column":"assets","output":"million","target_scale":1000000},
        {"op":"scale","column":"assets","output":"assets","target_scale":1000000000}]}}
    view=analysis_presentation(frame,manifest)
    assert view["columns"]==["period","assets"]
