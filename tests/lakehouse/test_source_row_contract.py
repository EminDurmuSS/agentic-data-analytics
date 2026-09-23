"""Commit 11: verified source-row contracts, uniform across EVDS/BDDK/PDF/web bulletin."""
import copy

import pytest

from agentic_analytics.lakehouse.source_contract import (
    CONTRACT_FIELDS,
    SourceContractError,
    SourceRowContractLedger,
    build_source_row_contract,
    validate_source_row_contract,
)

EVDS_BINDING = {
    "source_system": "TCMB_EVDS", "unit": "percent", "table": "evds.demo_core_rates_v1",
    "official_series_url": "", "source_metadata_url": "https://evds2.tcmb.gov.tr/",
    "institution_scope": "series_defined",
}
EVDS_CELL = {
    "source_response_file": "raw/TP.BKR.TRY.17_2021-01-01_2021-12-31_response.json.gz",
    "source_response_sha256": "a" * 64, "source_row_index": 5,
    "series_code": "TP.BKR.TRY.17", "period": "2021-01",
}

BDDK_BINDING = {
    "source_system": "BDDK_MONTHLY", "unit": "TRY", "table": "bddk.monthly_all_groups",
    "institution_scope": "BDDK_MONTHLY group namespace",
    "official_series_url": "https://www.bddk.org.tr/BultenAylik",
}
BDDK_CELL = {
    "source_file": "bddk_aylik_bulten_202101.xlsx", "source_sha256": "b" * 64,
    "source_row_index": 12, "value_dimension": "Toplam", "source_value": 123.4,
    "metric_code": "table03:TasitKredisi", "group_code": 1,
}

TUIK_BINDING = {
    "source_system": "TUIK_DATA_PORTAL", "unit": "count", "table": "tuik_province_housing_sales",
    "source_metadata_url": "https://veriportali.tuik.gov.tr/", "geography_scope": "province_grain",
}
TUIK_CELL = {
    "source_csv_file": "tuik_konut_satis_202101.csv", "source_csv_sha256": "c" * 64,
    "source_row_index": 3, "source_column_index": 2, "source_cell": "B3",
    "source_sheet": "Sayfa1", "source_press_url": "https://data.tuik.gov.tr/Bulten/Index?p=58340",
}

PDF_BINDING = {
    "source_system": "PDF_DOCUMENT", "unit": "TRY",
    "source_url": "https://www.borsaistanbul.com/files/kiymetli-madenler.pdf",
    "table": "table_001", "institution_scope": "BIST Kiymetli Madenler Piyasasi",
}
PDF_CELL = {
    "raw_sha256": "d" * 64, "table_id": "table_001", "row": 4, "page_number": 7,
}


@pytest.mark.parametrize("binding,cell,column,period", [
    (EVDS_BINDING, EVDS_CELL, "vehicle_loan_rate", "2021-01"),
    (BDDK_BINDING, BDDK_CELL, "tasit_kredisi", "2021-01"),
    (TUIK_BINDING, TUIK_CELL, "housing_sales_count", "2021-01"),
    (PDF_BINDING, PDF_CELL, "islem_hacmi", "2023-01"),
])
def test_every_real_source_kind_produces_a_complete_contract(binding, cell, column, period):
    contract = build_source_row_contract(binding, cell, column=column, period=period, value=42.0)
    assert set(contract) == {*CONTRACT_FIELDS, "complete", "missing_fields"}
    assert contract["complete"] is True
    assert contract["missing_fields"] == []
    assert contract["hash"] is not None
    assert contract["column"] == column
    assert contract["period"] == period
    assert contract["value"] == 42.0
    validate_source_row_contract(contract)  # re-validates the built shape


def test_evds_contract_uses_the_response_locator_and_series_code_row():
    contract = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
    assert contract["hash"] == "a" * 64
    assert contract["row"] == 5
    assert contract["url"] == "https://evds2.tcmb.gov.tr/"
    assert contract["scope"] == "series_defined"


def test_bddk_contract_uses_the_bulten_file_and_group_scope():
    contract = build_source_row_contract(BDDK_BINDING, BDDK_CELL, column="x", period="2021-01", value=1)
    assert contract["hash"] == "b" * 64
    assert contract["table"] == "bddk.monthly_all_groups"
    assert contract["scope"] == "BDDK_MONTHLY group namespace"


def test_tuik_web_bulletin_contract_captures_sheet_and_press_url():
    contract = build_source_row_contract(TUIK_BINDING, TUIK_CELL, column="x", period="2021-01", value=1)
    assert contract["hash"] == "c" * 64
    assert contract["page_or_sheet"] == "Sayfa1"
    assert contract["url"] == "https://data.tuik.gov.tr/Bulten/Index?p=58340"


def test_pdf_document_contract_captures_page_and_table_id():
    contract = build_source_row_contract(PDF_BINDING, PDF_CELL, column="x", period="2023-01", value=1)
    assert contract["hash"] == "d" * 64
    assert contract["page_or_sheet"] == 7
    assert contract["table"] == "table_001"
    assert contract["url"].startswith("https://www.borsaistanbul.com/")


def test_missing_hash_makes_the_contract_incomplete_not_silently_dropped():
    cell = {k: v for k, v in EVDS_CELL.items() if k != "source_response_sha256"}
    contract = build_source_row_contract(EVDS_BINDING, cell, column="x", period="2021-01", value=1)
    assert contract["complete"] is False
    assert "hash" in contract["missing_fields"]


def test_source_id_is_deterministic_and_does_not_change_with_the_delivered_value():
    a = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
    b = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=999)
    assert a["source_id"] == b["source_id"]


def test_source_id_changes_with_row_or_column_identity():
    a = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
    other_cell = {**EVDS_CELL, "source_row_index": 6}
    b = build_source_row_contract(EVDS_BINDING, other_cell, column="x", period="2021-01", value=1)
    assert a["source_id"] != b["source_id"]


def test_build_rejects_non_mapping_inputs():
    with pytest.raises(SourceContractError):
        build_source_row_contract(None, EVDS_CELL, column="x", period="2021-01", value=1)
    with pytest.raises(SourceContractError):
        build_source_row_contract(EVDS_BINDING, "not a dict", column="x", period="2021-01", value=1)


def test_build_rejects_empty_column_or_period():
    with pytest.raises(SourceContractError):
        build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="", period="2021-01", value=1)
    with pytest.raises(SourceContractError):
        build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="", value=1)


def test_validate_rejects_malformed_contracts():
    good = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
    with pytest.raises(SourceContractError):
        validate_source_row_contract({**good, "source_id": "not-content-addressed"})
    with pytest.raises(SourceContractError):
        validate_source_row_contract({**good, "hash": 12345})
    with pytest.raises(SourceContractError):
        validate_source_row_contract({**good, "period": ""})
    with pytest.raises(SourceContractError):
        validate_source_row_contract({**good, "complete": "yes"})
    with pytest.raises(SourceContractError):
        validate_source_row_contract({**good, "missing_fields": ["value"]})  # value is not required
    with pytest.raises(SourceContractError):
        validate_source_row_contract({key: good[key] for key in good if key != "hash"})  # missing key


class TestSourceRowContractLedger:
    def test_persist_and_get_round_trips(self, tmp_path):
        ledger = SourceRowContractLedger(tmp_path)
        contract = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
        ledger.persist(contract)
        assert ledger.exists(contract["source_id"])
        assert ledger.get(contract["source_id"]) == contract

    def test_persist_is_idempotent_for_byte_identical_contracts(self, tmp_path):
        ledger = SourceRowContractLedger(tmp_path)
        contract = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
        ledger.persist(contract)
        ledger.persist(copy.deepcopy(contract))  # must not raise

    def test_persist_rejects_a_conflicting_rewrite_under_the_same_id(self, tmp_path):
        ledger = SourceRowContractLedger(tmp_path)
        contract = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
        ledger.persist(contract)
        tampered = {**contract, "value": 999}  # same source_id, different content
        with pytest.raises(SourceContractError):
            ledger.persist(tampered)

    def test_get_on_unknown_id_returns_none(self, tmp_path):
        ledger = SourceRowContractLedger(tmp_path)
        contract = build_source_row_contract(EVDS_BINDING, EVDS_CELL, column="x", period="2021-01", value=1)
        assert ledger.get(contract["source_id"]) is None

    def test_persist_rejects_an_invalid_contract(self, tmp_path):
        ledger = SourceRowContractLedger(tmp_path)
        with pytest.raises(SourceContractError):
            ledger.persist({"not": "a contract"})
