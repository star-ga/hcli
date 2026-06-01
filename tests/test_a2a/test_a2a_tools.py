"""Pruebas de las herramientas del agente A2A: funciones de memoria,
seguridad y FHIR."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.clinical_memory import HistoriaClinicaEngine

# Importa por adelantado los módulos A2A para que patch.object() pueda apuntarlos
import a2a_agent.tools.memory_tools as _mem_mod
import a2a_agent.tools.safety_tools as _safety_mod
import a2a_agent.tools.fhir_tools as _fhir_mod

FIXTURES = Path(__file__).parent.parent / "fixtures"
BUNDLE = json.loads((FIXTURES / "lucia_obono_bundle.json").read_text())
PATIENT_ID = "patient-lucia-obono"

# Agrupa los recursos por tipo para las respuestas FHIR simuladas
_BY_TYPE: dict[str, list[dict]] = {}
for _entry in BUNDLE["entry"]:
    _res = _entry["resource"]
    _BY_TYPE.setdefault(_res["resourceType"], []).append(_res)


def _make_search_bundle(resources: list[dict]) -> dict:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }


def _mock_fhir_get(url: str, **kwargs) -> MagicMock:
    """Encamina las peticiones GET simuladas al tipo de recurso correcto."""
    resp = MagicMock()
    resp.status_code = 200

    base = "https://fhir.example.com/r4/"
    rel_path = url.replace(base, "").split("?")[0]
    resource_type = rel_path.split("/")[0]

    if resource_type == "Patient" and "/" in rel_path:
        resp.json.return_value = _BY_TYPE["Patient"][0]
    elif resource_type == "MedicationRequest":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("MedicationRequest", []))
    elif resource_type == "Condition":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("Condition", []))
    elif resource_type == "AllergyIntolerance":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("AllergyIntolerance", []))
    elif resource_type == "Observation":
        params = kwargs.get("params", {})
        category = params.get("category", "")
        obs = _BY_TYPE.get("Observation", [])
        if category:
            obs = [
                o for o in obs
                if any(
                    cat.get("coding", [{}])[0].get("code") == category
                    for cat in o.get("category", [])
                )
            ]
        resp.json.return_value = _make_search_bundle(obs)
    elif resource_type == "Encounter":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("Encounter", []))
    else:
        resp.status_code = 404
        resp.raise_for_status.side_effect = Exception("404 Not Found")
    return resp


def _make_tool_context(patient_id: str = PATIENT_ID, with_fhir: bool = False) -> MagicMock:
    """Crea un ToolContext simulado con estado de sesión."""
    ctx = MagicMock()
    state = {"patient_id": patient_id}
    if with_fhir:
        state["fhir_url"] = "https://fhir.example.com/r4"
        state["fhir_token"] = "test-token"
    ctx.state = state
    return ctx


@pytest.fixture
def engine(tmp_path):
    """Motor nuevo con el paquete de Lucía Obono Mangue precargado."""
    eng = HistoriaClinicaEngine(data_dir=str(tmp_path / "hcli"))
    eng.ingest_from_bundle(BUNDLE, PATIENT_ID)
    return eng


# ══════════════════════════════════════════════════════════════════════════════
# Herramientas de memoria
# ══════════════════════════════════════════════════════════════════════════════


class TestRecallClinicalContext:
    def test_returns_results(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.recall_clinical_context("warfarin bleeding", ctx)
        assert result["status"] == "success"
        assert result["result_count"] > 0

    def test_confidence_structure(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.recall_clinical_context("diabetes management", ctx)
        conf = result["confidence"]
        assert "score" in conf
        assert "level" in conf
        assert "should_abstain" in conf
        assert "reason" in conf

    def test_empty_patient_abstains(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context(patient_id="nonexistent")
            result = _mem_mod.recall_clinical_context("any query", ctx)
        assert result["confidence"]["should_abstain"] is True
        assert result["result_count"] == 0

    def test_no_patient_id_returns_error(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = MagicMock()
            ctx.state = {}
            with patch.dict(os.environ, {"DEMO_MODE": ""}):
                result = _mem_mod.recall_clinical_context("test", ctx)
        assert result["status"] == "error"

    def test_top_k_limit(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.recall_clinical_context("medication", ctx, top_k=2)
        assert result["result_count"] <= 2

    def test_returns_audit_hash(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.recall_clinical_context("allergies", ctx)
        assert len(result["audit_hash"]) == 64


class TestStoreClinicalNote:
    def test_stores_note(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.store_clinical_note(
                "Seguimiento", "Glucemia en mejoría.", "clinical_note", ctx,
            )
        assert result["status"] == "success"
        assert "block_id" in result
        assert result["block_id"].startswith("note-")

    def test_returns_audit_hash(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _mem_mod.store_clinical_note(
                "Laboratorio", "HbA1c 7,2 %", "lab_result", ctx,
            )
        assert len(result["audit_hash"]) == 64

    def test_no_patient_id_returns_error(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = MagicMock()
            ctx.state = {}
            with patch.dict(os.environ, {"DEMO_MODE": ""}):
                result = _mem_mod.store_clinical_note(
                    "Título", "Contenido", "clinical_note", ctx,
                )
        assert result["status"] == "error"

    def test_stored_note_recallable(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            _mem_mod.store_clinical_note(
                "Marcador de prueba único XYZ123",
                "Esta nota contiene el texto único XYZ123.",
                "clinical_note",
                ctx,
            )
            result = _mem_mod.recall_clinical_context("XYZ123", ctx)
        assert result["result_count"] > 0
        assert any("XYZ123" in b["content"] for b in result["results"])


# ══════════════════════════════════════════════════════════════════════════════
# Herramientas de seguridad
# ══════════════════════════════════════════════════════════════════════════════


class TestMedicationSafetyReview:
    def test_detects_interactions(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert result["status"] == "success"
        assert result["interaction_count"] >= 1

    def test_detects_allergy_conflicts(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert result["allergy_conflict_count"] >= 1
        pairs = {
            (c["allergen"], c["prescribed_medication"])
            for c in result["allergy_conflicts"]
        }
        assert ("penicillin", "amoxicillin") in pairs

    def test_has_critical_findings(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert result["critical_findings"] >= 1

    def test_confidence_structure(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert "score" in result["confidence"]
        assert "level" in result["confidence"]

    def test_has_summary(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert "interacción" in result["summary"].lower()

    def test_no_patient_id_returns_error(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = MagicMock()
            ctx.state = {}
            with patch.dict(os.environ, {"DEMO_MODE": ""}):
                result = _safety_mod.medication_safety_review(ctx)
        assert result["status"] == "error"

    def test_medications_reviewed_list(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.medication_safety_review(ctx)
        assert result["medication_count"] == 7
        assert "Warfarin 5mg" in result["medications_reviewed"]


class TestDetectRecordContradictions:
    def test_finds_contradictions(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.detect_record_contradictions(ctx)
        assert result["status"] == "success"
        assert result["contradiction_count"] >= 2

    def test_types_found(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.detect_record_contradictions(ctx)
        assert "allergy_medication_conflict" in result["types_found"]
        assert "drug_interaction" in result["types_found"]

    def test_has_critical_and_high(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.detect_record_contradictions(ctx)
        assert result["has_critical"] is True
        assert result["critical_count"] >= 1

    def test_escalation_message(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.detect_record_contradictions(ctx)
        assert result["escalation"] is not None
        assert "IMMEDIATE" in result["escalation"] or "PRIORITY" in result["escalation"]

    def test_chain_integrity(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.detect_record_contradictions(ctx)
        assert result["chain_integrity"] == "verified"

    def test_no_patient_id_returns_error(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = MagicMock()
            ctx.state = {}
            with patch.dict(os.environ, {"DEMO_MODE": ""}):
                result = _safety_mod.detect_record_contradictions(ctx)
        assert result["status"] == "error"

    def test_empty_patient_no_contradictions(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context(patient_id="nonexistent")
            result = _safety_mod.detect_record_contradictions(ctx)
        assert result["contradiction_count"] == 0
        assert result["escalation"] is None


class TestExplainClinicalConflictA2A:
    def test_returns_narrative(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.explain_clinical_conflict(ctx, conflict_index=0)
        assert result["status"] == "success"
        assert len(result["narrative"]) > 0

    def test_abstains_when_no_conflicts(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context(patient_id="nonexistent")
            result = _safety_mod.explain_clinical_conflict(ctx, conflict_index=0)
        assert result["abstained"] is True
        assert "ABSTENCIÓN" in result["narrative"]

    def test_invalid_index_abstains(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.explain_clinical_conflict(ctx, conflict_index=999)
        assert result["abstained"] is True

    def test_has_chain_integrity(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = _make_tool_context()
            result = _safety_mod.explain_clinical_conflict(ctx, conflict_index=0)
        assert result["chain_integrity"] == "verified"

    def test_no_patient_id_returns_error(self, engine):
        with patch.object(_mem_mod, "_engine", engine):
            ctx = MagicMock()
            ctx.state = {}
            with patch.dict(os.environ, {"DEMO_MODE": ""}):
                result = _safety_mod.explain_clinical_conflict(ctx, conflict_index=0)
        assert result["status"] == "error"


# ══════════════════════════════════════════════════════════════════════════════
# Herramientas FHIR
# ══════════════════════════════════════════════════════════════════════════════


class TestGetPatientDemographics:
    def test_returns_patient_data(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_patient_demographics(ctx)
        assert result["status"] == "success"
        assert "Obono Mangue" in result["name"]
        assert result["gender"] == "female"

    def test_missing_fhir_context_returns_error(self):
        ctx = _make_tool_context()
        result = _fhir_mod.get_patient_demographics(ctx)
        assert result["status"] == "error"
        assert "fhir_url" in result["error_message"]

    def test_returns_birth_date(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_patient_demographics(ctx)
        assert result["birth_date"] == "1959-04-12"


class TestGetActiveMedications:
    def test_returns_medications(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_active_medications(ctx)
        assert result["status"] == "success"
        assert result["count"] == 7
        names = [m["medication"] for m in result["medications"]]
        assert "Warfarin 5mg" in names

    def test_missing_fhir_context_returns_error(self):
        ctx = _make_tool_context()
        result = _fhir_mod.get_active_medications(ctx)
        assert result["status"] == "error"

    def test_medication_has_dosage(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_active_medications(ctx)
        for med in result["medications"]:
            assert "dosage" in med


class TestGetActiveConditions:
    def test_returns_conditions(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_active_conditions(ctx)
        assert result["status"] == "success"
        assert result["count"] == 4
        names = [c["condition"] for c in result["conditions"]]
        assert "Type 2 Diabetes Mellitus" in names

    def test_missing_fhir_context_returns_error(self):
        ctx = _make_tool_context()
        result = _fhir_mod.get_active_conditions(ctx)
        assert result["status"] == "error"

    def test_condition_has_severity(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_active_conditions(ctx)
        for cond in result["conditions"]:
            assert "severity" in cond


class TestGetRecentObservations:
    def test_returns_vital_signs(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_recent_observations("vital-signs", ctx)
        assert result["status"] == "success"
        assert result["category"] == "vital-signs"
        assert len(result["observations"]) == 2

    def test_returns_labs(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_recent_observations("laboratory", ctx)
        assert result["status"] == "success"
        assert result["category"] == "laboratory"
        assert len(result["observations"]) == 5

    def test_missing_fhir_context_returns_error(self):
        ctx = _make_tool_context()
        result = _fhir_mod.get_recent_observations("vital-signs", ctx)
        assert result["status"] == "error"

    def test_observation_has_value(self):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            ctx = _make_tool_context(with_fhir=True)
            result = _fhir_mod.get_recent_observations("laboratory", ctx)
        for obs in result["observations"]:
            assert "value" in obs
            assert "observation" in obs


# ══════════════════════════════════════════════════════════════════════════════
# Herramientas FHIR -- Pruebas de funciones auxiliares
# ══════════════════════════════════════════════════════════════════════════════


class TestFHIRHelpers:
    def test_coding_display_returns_first(self):
        assert _fhir_mod._coding_display([{"display": "Warfarin"}]) == "Warfarin"

    def test_coding_display_unknown_fallback(self):
        assert _fhir_mod._coding_display([{}]) == "Unknown"

    def test_coding_display_empty_list(self):
        assert _fhir_mod._coding_display([]) == "Unknown"

    def test_get_fhir_context_missing_fields(self):
        ctx = MagicMock()
        ctx.state = {"patient_id": "pid"}  # Faltan fhir_url y fhir_token
        result = _fhir_mod._get_fhir_context(ctx)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "fhir_url" in result["error_message"]

    def test_get_fhir_context_all_present(self):
        ctx = MagicMock()
        ctx.state = {
            "fhir_url": "https://fhir.example.com/r4",
            "fhir_token": "tok",
            "patient_id": "pid",
        }
        result = _fhir_mod._get_fhir_context(ctx)
        assert isinstance(result, tuple)
        assert result == ("https://fhir.example.com/r4", "tok", "pid")
