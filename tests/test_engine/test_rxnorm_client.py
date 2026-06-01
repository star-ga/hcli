"""Tests for RxNorm API client — mocked HTTP responses."""
import pytest
import respx
import httpx

from engine.rxnorm_client import (
    resolve_rxcui,
    get_interactions_for_list,
    normalize_medication_list,
    RxConcept,
    RXNORM_BASE,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear lru_cache between tests."""
    resolve_rxcui.cache_clear()
    from engine.rxnorm_client import _get_concept_properties
    _get_concept_properties.cache_clear()
    yield


class TestResolveRxcui:
    @respx.mock
    def test_exact_match(self):
        respx.get(f"{RXNORM_BASE}/rxcui.json").mock(
            return_value=httpx.Response(200, json={
                "idGroup": {"rxnormId": ["6809"]}
            })
        )
        respx.get(f"{RXNORM_BASE}/rxcui/6809/properties.json").mock(
            return_value=httpx.Response(200, json={
                "properties": {"rxcui": "6809", "name": "metformin", "tty": "IN"}
            })
        )
        result = resolve_rxcui("metformin")
        assert result is not None
        assert result.rxcui == "6809"
        assert result.name == "metformin"

    @respx.mock
    def test_approximate_match(self):
        respx.get(f"{RXNORM_BASE}/rxcui.json").mock(
            return_value=httpx.Response(200, json={"idGroup": {}})
        )
        respx.get(f"{RXNORM_BASE}/approximateTerm.json").mock(
            return_value=httpx.Response(200, json={
                "approximateGroup": {
                    "candidate": [{"rxcui": "6809", "score": "85"}]
                }
            })
        )
        respx.get(f"{RXNORM_BASE}/rxcui/6809/properties.json").mock(
            return_value=httpx.Response(200, json={
                "properties": {"rxcui": "6809", "name": "metformin", "tty": "IN"}
            })
        )
        result = resolve_rxcui("Glucophage")
        assert result is not None
        assert result.rxcui == "6809"

    @respx.mock
    def test_no_match(self):
        respx.get(f"{RXNORM_BASE}/rxcui.json").mock(
            return_value=httpx.Response(200, json={"idGroup": {}})
        )
        respx.get(f"{RXNORM_BASE}/approximateTerm.json").mock(
            return_value=httpx.Response(200, json={
                "approximateGroup": {"candidate": []}
            })
        )
        result = resolve_rxcui("notarealdrug")
        assert result is None

    def test_empty_input(self):
        assert resolve_rxcui("") is None
        assert resolve_rxcui("  ") is None


class TestGetInteractions:
    @respx.mock
    def test_interaction_found(self):
        respx.get(f"{RXNORM_BASE}/interaction/list.json").mock(
            return_value=httpx.Response(200, json={
                "fullInteractionTypeGroup": [{
                    "sourceName": "DrugBank",
                    "fullInteractionType": [{
                        "interactionPair": [{
                            "interactionConcept": [
                                {"minConceptItem": {"rxcui": "11289", "name": "warfarin"}},
                                {"minConceptItem": {"rxcui": "5640", "name": "ibuprofen"}},
                            ],
                            "severity": "high",
                            "description": "Increased risk of bleeding when warfarin is combined with ibuprofen",
                        }]
                    }]
                }]
            })
        )
        results = get_interactions_for_list(["11289", "5640"])
        assert len(results) >= 1
        assert results[0].drug_a == "warfarin"
        assert results[0].drug_b == "ibuprofen"
        assert results[0].severity == "serious"

    @respx.mock
    def test_no_interactions(self):
        respx.get(f"{RXNORM_BASE}/interaction/list.json").mock(
            return_value=httpx.Response(200, json={})
        )
        results = get_interactions_for_list(["6809", "83367"])
        assert results == []

    def test_single_rxcui(self):
        results = get_interactions_for_list(["6809"])
        assert results == []


class TestNormalizeMedicationList:
    @respx.mock
    def test_normalize(self):
        respx.get(f"{RXNORM_BASE}/rxcui.json").mock(
            return_value=httpx.Response(200, json={
                "idGroup": {"rxnormId": ["6809"]}
            })
        )
        respx.get(f"{RXNORM_BASE}/rxcui/6809/properties.json").mock(
            return_value=httpx.Response(200, json={
                "properties": {"rxcui": "6809", "name": "metformin", "tty": "IN"}
            })
        )
        result = normalize_medication_list(["metformin 500mg"])
        assert "metformin 500mg" in result
        assert result["metformin 500mg"] is not None
        assert result["metformin 500mg"].rxcui == "6809"
