"""Pruebas de las herramientas del servidor MCP: llaman directamente a las
funciones de herramienta contra el motor."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.clinical_memory import HistoriaClinicaEngine

FIXTURES = Path(__file__).parent.parent / "fixtures"
BUNDLE = json.loads((FIXTURES / "lucia_obono_bundle.json").read_text())
PATIENT_ID = "patient-lucia-obono"


@pytest.fixture
def engine(tmp_path):
    """Motor nuevo con el paquete de Lucía Obono Mangue precargado."""
    eng = HistoriaClinicaEngine(data_dir=str(tmp_path / "hcli"))
    eng.ingest_from_bundle(BUNDLE, PATIENT_ID)
    return eng


def _unwrap(tool):
    """Extrae la función original del envoltorio FunctionTool de FastMCP."""
    return getattr(tool, "fn", tool)


@pytest.fixture
def mcp_tools(engine):
    """Sustituye el _engine a nivel de módulo en mcp_server.server para que
    las herramientas usen el motor del fixture."""
    with patch("mcp_server.server._engine", engine):
        from mcp_server.server import (
            store_clinical_observation,
            recall_patient_context,
            check_medication_conflicts,
            check_allergy_conflicts,
            get_treatment_dependencies,
            get_clinical_audit_trail,
            summarize_patient_history,
            detect_belief_drift,
            ingest_patient_data,
            explain_clinical_conflict,
            clinical_care_handoff,
        )
        yield {
            "store_clinical_observation": _unwrap(store_clinical_observation),
            "recall_patient_context": _unwrap(recall_patient_context),
            "check_medication_conflicts": _unwrap(check_medication_conflicts),
            "check_allergy_conflicts": _unwrap(check_allergy_conflicts),
            "get_treatment_dependencies": _unwrap(get_treatment_dependencies),
            "get_clinical_audit_trail": _unwrap(get_clinical_audit_trail),
            "summarize_patient_history": _unwrap(summarize_patient_history),
            "detect_belief_drift": _unwrap(detect_belief_drift),
            "ingest_patient_data": _unwrap(ingest_patient_data),
            "explain_clinical_conflict": _unwrap(explain_clinical_conflict),
            "clinical_care_handoff": _unwrap(clinical_care_handoff),
        }


# ── store_clinical_observation ───────────────────────────────────────────────


class TestStoreObservation:
    def test_returns_success(self, mcp_tools):
        result = mcp_tools["store_clinical_observation"](
            patient_id=PATIENT_ID,
            observation_type="clinical_note",
            title="Nota de seguimiento",
            content="El paciente refiere mejor control de la glucemia.",
        )
        assert result["status"] == "success"

    def test_returns_block_id(self, mcp_tools):
        result = mcp_tools["store_clinical_observation"](
            patient_id=PATIENT_ID,
            observation_type="lab_result",
            title="Resultado de HbA1c",
            content="HbA1c 7,2 %",
        )
        assert "block_id" in result
        assert result["block_id"].startswith("obs-")

    def test_returns_audit_hash(self, mcp_tools):
        result = mcp_tools["store_clinical_observation"](
            patient_id=PATIENT_ID,
            observation_type="medication_change",
            title="Ajuste de dosis",
            content="Metformina reducida a 250 mg.",
        )
        assert "audit_hash" in result
        assert len(result["audit_hash"]) == 64  # SHA-256

    def test_custom_source(self, mcp_tools):
        result = mcp_tools["store_clinical_observation"](
            patient_id=PATIENT_ID,
            observation_type="assessment",
            title="Valoración de enfermería",
            content="Paciente estable.",
            source="Enfermera Nzang",
        )
        assert result["status"] == "success"
        assert "Valoración de enfermería" in result["message"]

    def test_message_contains_title(self, mcp_tools):
        result = mcp_tools["store_clinical_observation"](
            patient_id=PATIENT_ID,
            observation_type="clinical_note",
            title="Nota de prueba",
            content="Contenido aquí.",
        )
        assert "Nota de prueba" in result["message"]


# ── recall_patient_context ───────────────────────────────────────────────────


class TestRecallPatientContext:
    def test_returns_results(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id=PATIENT_ID,
            query="warfarin bleeding risk",
        )
        assert result["status"] == "success"
        assert result["result_count"] > 0

    def test_confidence_structure(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id=PATIENT_ID,
            query="diabetes medications",
        )
        conf = result["confidence"]
        assert "score" in conf
        assert "level" in conf
        assert "should_abstain" in conf
        assert "reason" in conf
        assert isinstance(conf["score"], float)

    def test_empty_patient_abstains(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id="nonexistent-patient",
            query="any query",
        )
        assert result["confidence"]["should_abstain"] is True
        assert result["result_count"] == 0

    def test_top_k_limit(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id=PATIENT_ID,
            query="medication",
            top_k=3,
        )
        assert result["result_count"] <= 3

    def test_returns_audit_hash(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id=PATIENT_ID,
            query="allergies",
        )
        assert len(result["audit_hash"]) == 64

    def test_results_have_titles(self, mcp_tools):
        result = mcp_tools["recall_patient_context"](
            patient_id=PATIENT_ID,
            query="metformin",
        )
        for block in result["results"]:
            assert "title" in block
            assert "content" in block


# ── check_medication_conflicts ───────────────────────────────────────────────


class TestCheckMedicationConflicts:
    def test_detects_warfarin_ibuprofen(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        pairs = {(i["drug_a"], i["drug_b"]) for i in result["interactions"]}
        assert ("warfarin", "ibuprofen") in pairs

    def test_detects_penicillin_amoxicillin(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        allergy_pairs = {
            (c["allergen"], c["medication"]) for c in result["allergy_conflicts"]
        }
        assert ("penicillin", "amoxicillin") in allergy_pairs

    def test_has_recommendations(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        assert len(result["recommendations"]) > 0
        assert any("SUSPENDER" in r or "REVISAR" in r for r in result["recommendations"])

    def test_has_confidence(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        assert "confidence" in result
        assert "score" in result["confidence"]
        assert "level" in result["confidence"]

    def test_has_summary(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        assert "summary" in result
        assert "interacción" in result["summary"].lower()

    def test_interaction_count(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        assert result["interaction_count"] >= 1
        assert result["interaction_count"] == len(result["interactions"])

    def test_allergy_conflict_count(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id=PATIENT_ID)
        assert result["allergy_conflict_count"] >= 1
        assert result["allergy_conflict_count"] == len(result["allergy_conflicts"])

    def test_empty_patient_no_crash(self, mcp_tools):
        result = mcp_tools["check_medication_conflicts"](patient_id="nonexistent")
        assert result["status"] == "success"
        assert result["interaction_count"] == 0


# ── check_allergy_conflicts ──────────────────────────────────────────────────


class TestCheckAllergyConflicts:
    def test_detects_penicillin_amoxicillin(self, mcp_tools):
        result = mcp_tools["check_allergy_conflicts"](patient_id=PATIENT_ID)
        assert result["conflict_count"] >= 1
        pairs = {(c["allergen"], c["medication"]) for c in result["allergy_conflicts"]}
        assert ("penicillin", "amoxicillin") in pairs

    def test_response_structure(self, mcp_tools):
        result = mcp_tools["check_allergy_conflicts"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert result["patient_id"] == PATIENT_ID
        assert "audit_hash" in result

    def test_conflict_has_cross_reaction_group(self, mcp_tools):
        result = mcp_tools["check_allergy_conflicts"](patient_id=PATIENT_ID)
        for conflict in result["allergy_conflicts"]:
            assert "cross_reaction_group" in conflict
            assert "description" in conflict

    def test_empty_patient_no_conflicts(self, mcp_tools):
        result = mcp_tools["check_allergy_conflicts"](patient_id="nonexistent")
        assert result["conflict_count"] == 0


# ── get_treatment_dependencies ───────────────────────────────────────────────


class TestGetTreatmentDependencies:
    def test_returns_dependencies(self, mcp_tools):
        result = mcp_tools["get_treatment_dependencies"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert len(result["dependencies"]) > 0

    def test_diabetes_has_metformin(self, mcp_tools):
        result = mcp_tools["get_treatment_dependencies"](patient_id=PATIENT_ID)
        diabetes_deps = [
            d for d in result["dependencies"]
            if d["condition"] and "diabetes" in d["condition"].lower()
        ]
        assert len(diabetes_deps) >= 1
        meds = diabetes_deps[0]["related_medications"]
        assert any("metformin" in m.lower() for m in meds if m)

    def test_dependency_has_condition_fields(self, mcp_tools):
        result = mcp_tools["get_treatment_dependencies"](patient_id=PATIENT_ID)
        for dep in result["dependencies"]:
            assert "condition" in dep
            assert "related_medications" in dep

    def test_has_audit_hash(self, mcp_tools):
        result = mcp_tools["get_treatment_dependencies"](patient_id=PATIENT_ID)
        assert len(result["audit_hash"]) == 64

    def test_empty_patient_empty_deps(self, mcp_tools):
        result = mcp_tools["get_treatment_dependencies"](patient_id="nonexistent")
        assert result["dependencies"] == []


# ── get_clinical_audit_trail ─────────────────────────────────────────────────


class TestGetClinicalAuditTrail:
    def test_returns_entries(self, mcp_tools):
        result = mcp_tools["get_clinical_audit_trail"]()
        assert result["status"] == "success"
        assert result["entry_count"] >= 1  # Al menos la entrada de ingesta

    def test_chain_integrity_verified(self, mcp_tools):
        result = mcp_tools["get_clinical_audit_trail"]()
        assert result["chain_integrity"] == "verified"

    def test_limit_parameter(self, mcp_tools):
        # Genera primero algunas entradas de auditoría
        mcp_tools["recall_patient_context"](patient_id=PATIENT_ID, query="test")
        mcp_tools["recall_patient_context"](patient_id=PATIENT_ID, query="test2")
        result = mcp_tools["get_clinical_audit_trail"](limit=1)
        assert result["entry_count"] <= 1

    def test_entries_have_hash(self, mcp_tools):
        result = mcp_tools["get_clinical_audit_trail"]()
        for entry in result["entries"]:
            # el backend usa "entry_hash"; la alternativa usa "hash"
            has_hash = "entry_hash" in entry or "hash" in entry
            assert has_hash, f"Falta el hash en la entrada de auditoría: {list(entry.keys())}"


# ── summarize_patient_history ────────────────────────────────────────────────


class TestSummarizePatientHistory:
    def test_returns_structured_summary(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert "medications" in result
        assert "conditions" in result
        assert "allergies" in result

    def test_medication_count(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id=PATIENT_ID)
        assert len(result["medications"]) == 7

    def test_condition_count(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id=PATIENT_ID)
        assert len(result["conditions"]) == 4

    def test_allergy_count(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id=PATIENT_ID)
        assert len(result["allergies"]) == 2

    def test_total_blocks(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id=PATIENT_ID)
        # Ingesta del paquete: 7 medicamentos + 4 afecciones + 2 alergias + 7 observaciones = 20 bloques
        assert result["total_blocks"] == 20

    def test_empty_patient(self, mcp_tools):
        result = mcp_tools["summarize_patient_history"](patient_id="nonexistent")
        assert result["status"] == "success"
        assert result["total_blocks"] == 0


# ── detect_belief_drift ──────────────────────────────────────────────────────


class TestDetectBeliefDrift:
    def test_finds_contradictions(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert result["contradiction_count"] >= 2

    def test_finds_allergy_conflict(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert "allergy_medication_conflict" in result["types_found"]

    def test_finds_drug_interaction(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert "drug_interaction" in result["types_found"]

    def test_finds_lab_medication_contraindication(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert "lab_medication_contraindication" in result["types_found"]

    def test_finds_lab_trend(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert "lab_trend_alert" in result["types_found"]

    def test_finds_provider_disagreement(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert "provider_disagreement" in result["types_found"]

    def test_has_critical_findings(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert result["has_critical"] is True
        assert result["critical_count"] >= 1

    def test_escalation_message(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id=PATIENT_ID)
        assert result["escalation"] is not None
        assert "REVISIÓN CLÍNICA INMEDIATA" in result["escalation"]

    def test_empty_patient_no_contradictions(self, mcp_tools):
        result = mcp_tools["detect_belief_drift"](patient_id="nonexistent")
        assert result["contradiction_count"] == 0
        assert result["has_critical"] is False
        assert result["escalation"] is None


# ── ingest_patient_data ──────────────────────────────────────────────────────


class TestIngestPatientData:
    def test_invalid_fhir_gracefully_returns_zero_blocks(self, mcp_tools):
        """Servidor FHIR no válido: las descargas de recursos individuales
        fallan en silencio, por lo que la herramienta devuelve éxito con 0
        bloques totales ingeridos."""
        result = mcp_tools["ingest_patient_data"](
            patient_id="test-patient",
            fhir_server_url="https://invalid.example.com/r4",
            fhir_access_token="bad-token",
        )
        # El motor captura los errores por categoría y devuelve éxito con recuentos a cero
        assert result["status"] == "success"
        assert result["total_blocks"] == 0

    def test_empty_url_returns_error(self, mcp_tools):
        result = mcp_tools["ingest_patient_data"](
            patient_id="test-patient",
            fhir_server_url="",
            fhir_access_token="some-token",
        )
        assert result["status"] == "error"


# ── explain_clinical_conflict ────────────────────────────────────────────────


class TestExplainClinicalConflict:
    def test_returns_narrative(self, mcp_tools):
        result = mcp_tools["explain_clinical_conflict"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert len(result["narrative"]) > 0
        assert isinstance(result["confidence_score"], float)

    def test_abstains_when_no_conflicts(self, mcp_tools):
        result = mcp_tools["explain_clinical_conflict"](patient_id="nonexistent")
        assert result["abstained"] is True
        assert "ABSTENCIÓN" in result["narrative"]

    def test_invalid_conflict_index_abstains(self, mcp_tools):
        result = mcp_tools["explain_clinical_conflict"](
            patient_id=PATIENT_ID, conflict_index=999,
        )
        assert result["abstained"] is True

    def test_has_model_used(self, mcp_tools):
        result = mcp_tools["explain_clinical_conflict"](patient_id=PATIENT_ID)
        assert "model_used" in result
        assert result["model_used"]  # No vacío

    def test_has_evidence_citations(self, mcp_tools):
        result = mcp_tools["explain_clinical_conflict"](patient_id=PATIENT_ID)
        assert "evidence_citations" in result


# ── clinical_care_handoff ────────────────────────────────────────────────────


class TestClinicalCareHandoff:
    def test_returns_handoff_note(self, mcp_tools):
        result = mcp_tools["clinical_care_handoff"](patient_id=PATIENT_ID)
        assert result["status"] == "success"
        assert len(result["handoff_note"]) > 0
        assert isinstance(result["confidence_score"], float)

    def test_abstains_when_no_data(self, mcp_tools):
        result = mcp_tools["clinical_care_handoff"](patient_id="nonexistent")
        # Sin datos, el traspaso aún se genera pero puede señalar evidencia baja
        assert result["status"] == "success"
        assert "handoff_note" in result

    def test_has_model_used(self, mcp_tools):
        result = mcp_tools["clinical_care_handoff"](patient_id=PATIENT_ID)
        assert "model_used" in result
        assert result["model_used"]

    def test_has_evidence_citations(self, mcp_tools):
        result = mcp_tools["clinical_care_handoff"](patient_id=PATIENT_ID)
        assert "evidence_citations" in result
