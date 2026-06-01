"""Tests for UMLS Metathesaurus cross-vocabulary mapper."""
import os
import pytest
import respx
import httpx

from engine.umls_mapper import (
    crosswalk,
    find_concept,
    are_same_concept,
    UMLSConcept,
    UMLS_BASE,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear lru_cache between tests."""
    crosswalk.cache_clear()
    find_concept.cache_clear()
    from engine.umls_mapper import _get_cui
    _get_cui.cache_clear()
    yield


@pytest.fixture
def mock_api_key(monkeypatch):
    """Set a mock UMLS API key."""
    monkeypatch.setattr("engine.umls_mapper.UMLS_API_KEY", "test-key-123")


class TestCrosswalk:
    @respx.mock
    def test_icd10_to_snomed(self, mock_api_key):
        respx.get(f"{UMLS_BASE}/crosswalk/current/source/ICD10CM/E11.9").mock(
            return_value=httpx.Response(200, json={
                "result": {
                    "results": [{
                        "ui": "44054006",
                        "name": "Type 2 diabetes mellitus",
                    }]
                }
            })
        )
        results = crosswalk("ICD10CM", "E11.9", "SNOMEDCT_US")
        assert len(results) == 1
        assert results[0].name == "Type 2 diabetes mellitus"

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr("engine.umls_mapper.UMLS_API_KEY", "")
        results = crosswalk("ICD10CM", "E11.9", "SNOMEDCT_US")
        assert results == []


class TestFindConcept:
    @respx.mock
    def test_search(self, mock_api_key):
        respx.get(f"{UMLS_BASE}/search/current").mock(
            return_value=httpx.Response(200, json={
                "result": {
                    "results": [{
                        "ui": "C0011849",
                        "name": "Diabetes Mellitus",
                        "rootSource": "SNOMEDCT_US",
                    }]
                }
            })
        )
        results = find_concept("diabetes")
        assert len(results) == 1
        assert results[0].cui == "C0011849"

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr("engine.umls_mapper.UMLS_API_KEY", "")
        results = find_concept("diabetes")
        assert results == []


class TestAreSameConcept:
    @respx.mock
    def test_same_concept(self, mock_api_key):
        respx.get(f"{UMLS_BASE}/content/current/source/ICD10CM/E11.9").mock(
            return_value=httpx.Response(200, json={
                "result": {"concept": "https://uts-ws.nlm.nih.gov/rest/content/current/CUI/C0011860"}
            })
        )
        respx.get(f"{UMLS_BASE}/content/current/source/SNOMEDCT_US/44054006").mock(
            return_value=httpx.Response(200, json={
                "result": {"concept": "https://uts-ws.nlm.nih.gov/rest/content/current/CUI/C0011860"}
            })
        )
        assert are_same_concept(("ICD10CM", "E11.9"), ("SNOMEDCT_US", "44054006")) is True

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr("engine.umls_mapper.UMLS_API_KEY", "")
        assert are_same_concept(("ICD10CM", "E11.9"), ("SNOMEDCT_US", "44054006")) is False
