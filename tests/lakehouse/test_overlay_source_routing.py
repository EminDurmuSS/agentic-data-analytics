"""Imported official origins remain discoverable under source routing filters."""
import pytest

from agentic_analytics.lakehouse.service import _source_match


@pytest.mark.parametrize("url,alias", [
    ("https://www.borsaistanbul.com/datum/prices.zip", "bist"),
    ("https://www.bddk.org.tr/data.csv", "bddk"),
    ("https://data.tuik.gov.tr/data.xlsx", "tuik"),
    ("https://evds2.tcmb.gov.tr/data.csv", "evds"),
    ("https://www.tcmb.gov.tr/report.pdf", "tcmb"),
    ("https://www.tbb.org.tr/report.pdf", "tbb"),
])
def test_official_overlay_url_routes_without_faking_native_system(url, alias):
    binding = {"source_system": "SESSION_DATASET", "source_url": url}
    assert _source_match(binding, [alias])["basis"] == "source_url_domain"
    assert binding["source_system"] == "SESSION_DATASET"


@pytest.mark.parametrize("url", [
    None, "", 123, {}, [], "https://example.org/borsaistanbul.com/prices.zip",
    "https://borsaistanbul.com.example.org/file", "https://fakeborsaistanbul.com/file",
    "https://borsaistanbul.com@example.org/file", "https://user@borsaistanbul.com/file",
    "file://borsaistanbul.com/file", "https://borsaistanbul.com:123/file",
    "https://borsaistanbul.com:invalid/file", "https://borsaistanbul.com\n/file",
])
def test_title_and_misleading_urls_cannot_claim_an_official_source(url):
    assert _source_match({"source_system": "SESSION_DATASET", "title": "BIST Banka Endeksi",
                          "source_url": url}, ["bist"]) is None


def test_institution_domain_does_not_imply_a_specific_subservice():
    assert _source_match({"source_system": "SESSION_DATASET", "source_url": "https://www.bddk.org.tr/file"}, ["finturk"]) is None
    assert _source_match({"source_system": "SESSION_DATASET", "source_url": "https://www.tcmb.gov.tr/file"}, ["evds"]) is None
