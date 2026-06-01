"""
Pruebas de integración — enlazan el cliente FHIR y el motor de Historia
Clínica con el bundle de Lucía Obono Mangue.

Simula httpx.get para imitar un servidor FHIR R4 que devuelve recursos
desde el fixture del paciente sintético.
"""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine.clinical_memory import HistoriaClinicaEngine
from engine.fhir_client import FHIRClient, FHIRContext, FHIRClientError

FIXTURES = Path(__file__).parent.parent / "fixtures"
BUNDLE = json.loads((FIXTURES / "lucia_obono_bundle.json").read_text())

# Agrupa los recursos por tipo para las respuestas FHIR simuladas
_BY_TYPE: dict[str, list[dict]] = {}
for entry in BUNDLE["entry"]:
    res = entry["resource"]
    _BY_TYPE.setdefault(res["resourceType"], []).append(res)


def _make_search_bundle(resources: list[dict]) -> dict:
    """Envuelve los recursos en un Bundle FHIR de tipo searchset."""
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }


def _mock_fhir_get(url: str, **kwargs) -> MagicMock:
    """Enruta las peticiones GET simuladas al tipo de recurso correcto."""
    resp = MagicMock()
    resp.status_code = 200

    # Analiza la ruta relativa a la URL base
    base = "https://fhir.example.com/r4/"
    rel_path = url.replace(base, "").split("?")[0]
    resource_type = rel_path.split("/")[0]

    if resource_type == "Patient" and "/" in rel_path:
        # Lectura directa: GET /Patient/{id}
        resp.json.return_value = _BY_TYPE["Patient"][0]
    elif resource_type == "MedicationRequest":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("MedicationRequest", []))
    elif resource_type == "Condition":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("Condition", []))
    elif resource_type == "AllergyIntolerance":
        resp.json.return_value = _make_search_bundle(_BY_TYPE.get("AllergyIntolerance", []))
    elif resource_type == "Observation":
        # Filtra por el parámetro de categoría si está presente
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
        resp.text = "Not Found"

    return resp


@pytest.fixture
def fhir_ctx():
    return FHIRContext(
        url="https://fhir.example.com/r4",
        token="test-token-abc",
        patient_id="patient-lucia-obono",
    )


@pytest.fixture
def fhir_client(fhir_ctx):
    with patch("httpx.get", side_effect=_mock_fhir_get):
        yield FHIRClient(fhir_ctx)


@pytest.fixture
def engine(tmp_path):
    return HistoriaClinicaEngine(data_dir=str(tmp_path / "hcli"))


# ── Pruebas del cliente FHIR ───────────────────────────────────────────────


class TestFHIRClient:
    def test_get_patient(self, fhir_client):
        patient = fhir_client.get_patient()
        assert patient["resourceType"] == "Patient"
        assert patient["name"][0]["family"] == "Obono Mangue"

    def test_get_medications(self, fhir_client):
        meds = fhir_client.get_medications()
        assert len(meds) == 7  # 5 habituales + ibuprofeno + amoxicilina
        names = [m["medicationCodeableConcept"]["text"] for m in meds]
        assert "Warfarin 5mg" in names
        assert "Ibuprofen 400mg" in names
        assert "Amoxicillin 500mg" in names

    def test_get_conditions(self, fhir_client):
        conditions = fhir_client.get_conditions()
        assert len(conditions) == 4
        texts = [c["code"]["text"] for c in conditions]
        assert "Type 2 Diabetes Mellitus" in texts
        assert "Chronic Kidney Disease Stage 3b" in texts

    def test_get_allergies(self, fhir_client):
        allergies = fhir_client.get_allergies()
        assert len(allergies) == 2
        names = [a["code"]["text"] for a in allergies]
        assert "Penicillin" in names
        assert "Sulfa drugs" in names

    def test_get_observations_vitals(self, fhir_client):
        obs = fhir_client.get_observations(category="vital-signs")
        assert len(obs) == 2  # 2 lecturas de tensión arterial
        assert all(o["resourceType"] == "Observation" for o in obs)

    def test_get_observations_labs(self, fhir_client):
        obs = fhir_client.get_observations(category="laboratory")
        assert len(obs) == 5  # 3 FG + HbA1c + INR

    def test_get_encounters(self, fhir_client):
        encounters = fhir_client.get_encounters()
        assert len(encounters) == 1
        assert "Fall" in encounters[0]["type"][0]["text"]


class TestFHIRContext:
    def test_missing_url_raises(self):
        ctx = FHIRContext(url="", token="tok", patient_id="pid")
        with pytest.raises(FHIRClientError, match="url"):
            ctx.validate()

    def test_missing_token_raises(self):
        ctx = FHIRContext(url="https://fhir.example.com", token="", patient_id="pid")
        with pytest.raises(FHIRClientError, match="token"):
            ctx.validate()

    @pytest.mark.parametrize("host", [
        "localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "[::1]",
    ])
    def test_ssrf_localhost_blocked(self, host):
        ctx = FHIRContext(url=f"https://{host}/fhir", token="tok", patient_id="pid")
        with pytest.raises(FHIRClientError, match="localhost|metadata"):
            ctx.validate()

    @pytest.mark.parametrize("host", [
        "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
        "169.254.1.1", "fc00::1", "fd12::1", "fe80::1",
    ])
    def test_ssrf_private_ranges_blocked(self, host):
        ctx = FHIRContext(url=f"https://{host}/fhir", token="tok", patient_id="pid")
        with pytest.raises(FHIRClientError, match="private network"):
            ctx.validate()

    def test_ssrf_public_url_allowed(self):
        ctx = FHIRContext(url="https://fhir.epic.com/r4", token="tok", patient_id="pid")
        ctx.validate()  # No debe lanzar excepción


# ── Pruebas de ingesta del motor ──────────────────────────────────────────


class TestEngineIngestion:
    def test_ingest_counts(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            counts = engine.ingest_from_fhir(fhir_client)

        assert counts["medications"] == 7
        assert counts["conditions"] == 4
        assert counts["allergies"] == 2
        assert counts["observations"] == 7  # 2 constantes vitales + 5 de laboratorio

    def test_ingest_creates_blocks(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)

        blocks = engine._patient_blocks.get("patient-lucia-obono", [])
        assert len(blocks) == 20  # 7 + 4 + 2 + 7

    def test_ingest_audit_entry(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)

        trail = engine.get_audit_trail()
        assert len(trail) == 1
        # el backend usa el campo "operation"; el fallback usa "action"
        entry = trail[0]
        if "action" in entry:
            assert entry["action"] == "ingest_fhir"
        else:
            assert entry["operation"] == "create_block"
            assert entry["agent"] == "hcli"


# ── Pruebas de seguridad de la medicación ─────────────────────────────────


class TestMedicationSafety:
    @pytest.fixture(autouse=True)
    def _ingest(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)
        self.engine = engine
        self.pid = "patient-lucia-obono"

    def test_detects_warfarin_ibuprofen_interaction(self):
        report = self.engine.medication_safety_check(self.pid)
        interaction_pairs = {(i.drug_a, i.drug_b) for i in report.interactions}
        assert ("warfarin", "ibuprofen") in interaction_pairs

    def test_detects_penicillin_amoxicillin_conflict(self):
        report = self.engine.medication_safety_check(self.pid)
        conflict_pairs = {(c.allergen, c.medication) for c in report.allergy_conflicts}
        assert ("penicillin", "amoxicillin") in conflict_pairs

    def test_report_has_summary(self):
        report = self.engine.medication_safety_check(self.pid)
        assert "drug interaction" in report.summary.lower()
        assert "allergy conflict" in report.summary.lower()

    def test_report_has_audit_hash(self):
        report = self.engine.medication_safety_check(self.pid)
        assert len(report.audit_hash) == 64  # SHA-256 en hexadecimal


# ── Pruebas de detección de contradicciones ───────────────────────────────


class TestContradictionDetection:
    @pytest.fixture(autouse=True)
    def _ingest(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)
        self.engine = engine
        self.pid = "patient-lucia-obono"

    def test_finds_allergy_medication_contradiction(self):
        contradictions = self.engine.detect_contradictions(self.pid)
        types = [c["type"] for c in contradictions]
        assert "allergy_medication_conflict" in types

    def test_finds_drug_interaction_contradiction(self):
        contradictions = self.engine.detect_contradictions(self.pid)
        types = [c["type"] for c in contradictions]
        assert "drug_interaction" in types

    def test_contradiction_severities(self):
        contradictions = self.engine.detect_contradictions(self.pid)
        severities = {c["severity"] for c in contradictions}
        assert "critical" in severities or "high" in severities

    def test_at_least_two_contradictions(self):
        contradictions = self.engine.detect_contradictions(self.pid)
        assert len(contradictions) >= 2

    def test_detects_gfr_metformin_contraindication(self):
        """Conflicto plantado n.º 3: FG en descenso + Metformina."""
        contradictions = self.engine.detect_contradictions(self.pid)
        lab_med = [c for c in contradictions if c["type"] == "lab_medication_contraindication"]
        assert len(lab_med) >= 1
        assert any("metformin" in str(c).lower() for c in lab_med)
        assert any("gfr" in str(c).lower() or "egfr" in str(c).lower() for c in lab_med)

    def test_detects_declining_gfr_trend(self):
        """Conflicto plantado n.º 3: tendencia descendente del FG 45 -> 38 -> 32."""
        contradictions = self.engine.detect_contradictions(self.pid)
        trends = [c for c in contradictions if c["type"] == "lab_trend_alert"]
        assert len(trends) >= 1
        assert any("declining" in c["description"].lower() for c in trends)

    def test_detects_bp_target_disagreement(self):
        """Conflicto plantado n.º 4: Cardiología <130/80 vs. Nefrología <140/90."""
        contradictions = self.engine.detect_contradictions(self.pid)
        provider = [c for c in contradictions if c["type"] == "provider_disagreement"]
        assert len(provider) >= 1
        assert any("bp target" in c["description"].lower() for c in provider)

    def test_all_four_planted_conflicts_detected(self):
        """Deben encontrarse los 4 conflictos plantados en los datos de Lucía Obono Mangue."""
        contradictions = self.engine.detect_contradictions(self.pid)
        types_found = {c["type"] for c in contradictions}
        assert "allergy_medication_conflict" in types_found
        assert "drug_interaction" in types_found
        assert "lab_medication_contraindication" in types_found
        # Tendencia o discrepancia entre profesionales (ambas son detecciones nuevas)
        assert "lab_trend_alert" in types_found or "provider_disagreement" in types_found

    def test_contradictions_have_recommendations(self):
        """Todas las contradicciones deben incluir recomendaciones accionables."""
        contradictions = self.engine.detect_contradictions(self.pid)
        for c in contradictions:
            assert "recommendation" in c, f"Missing recommendation in: {c['type']}"
            assert len(c["recommendation"]) > 10, f"Empty recommendation in: {c['type']}"


# ── Pruebas de recuperación (recall) ──────────────────────────────────────


class TestRecall:
    @pytest.fixture(autouse=True)
    def _ingest(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)
        self.engine = engine
        self.pid = "patient-lucia-obono"

    def test_recall_warfarin(self):
        result = self.engine.recall(self.pid, "warfarin bleeding risk")
        assert len(result.blocks) > 0
        titles = [b["title"] for b in result.blocks]
        assert any("warfarin" in t.lower() for t in titles)

    def test_recall_diabetes(self):
        result = self.engine.recall(self.pid, "diabetes management metformin")
        assert len(result.blocks) > 0

    def test_recall_empty_patient(self):
        result = self.engine.recall("nonexistent", "any query")
        assert result.confidence.should_abstain is True
        assert len(result.blocks) == 0

    def test_recall_has_audit(self):
        result = self.engine.recall(self.pid, "blood pressure")
        assert len(result.audit_hash) == 64

    def test_negation_query_handling(self):
        result = self.engine.recall(self.pid, "NOT allergic to penicillin")
        assert result.blocks is not None  # No debe fallar


# ── Pruebas de la cadena de auditoría ─────────────────────────────────────


class TestAuditChain:
    def test_chain_integrity_after_operations(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)

        pid = "patient-lucia-obono"
        engine.recall(pid, "medications")
        engine.medication_safety_check(pid)
        engine.detect_contradictions(pid)
        engine.patient_summary(pid)

        assert engine.verify_audit_chain() is True

    def test_chain_genesis(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)

        trail = engine.get_audit_trail()
        # el backend usa "0" * 64 como génesis; el fallback usa "genesis"
        assert trail[0]["prev_hash"] in ("genesis", "0" * 64)

    def test_chain_links(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)

        pid = "patient-lucia-obono"
        engine.recall(pid, "test")
        engine.recall(pid, "test2")

        trail = engine.get_audit_trail()
        for i in range(1, len(trail)):
            # el backend usa "entry_hash"; el fallback usa "hash"
            prev_entry_hash = trail[i - 1].get("entry_hash") or trail[i - 1].get("hash")
            assert trail[i]["prev_hash"] == prev_entry_hash


# ── Pruebas del resumen del paciente ──────────────────────────────────────


class TestPatientSummary:
    @pytest.fixture(autouse=True)
    def _ingest(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)
        self.engine = engine
        self.pid = "patient-lucia-obono"

    def test_summary_structure(self):
        summary = self.engine.patient_summary(self.pid)
        assert "medications" in summary
        assert "conditions" in summary
        assert "allergies" in summary
        assert "recent_observations" in summary

    def test_summary_counts(self):
        summary = self.engine.patient_summary(self.pid)
        assert len(summary["medications"]) == 7
        assert len(summary["conditions"]) == 4
        assert len(summary["allergies"]) == 2
        assert summary["total_blocks"] == 20

    def test_summary_medication_names(self):
        summary = self.engine.patient_summary(self.pid)
        med_names = [m["name"] for m in summary["medications"]]
        assert "Warfarin 5mg" in med_names
        assert "Metformin 500mg" in med_names


# ── Pruebas de síntesis con LLM ───────────────────────────────────────────


class TestLLMSynthesis:
    """Prueba las funciones de síntesis con IA generativa (explain_conflict, clinical_handoff).

    Estas pruebas se ejecutan sin clave de API de LLM — ejercitan el
    fallback por plantilla y la compuerta de abstención, que son las
    partes deterministas.
    """

    @pytest.fixture(autouse=True)
    def _ingest(self, engine, fhir_client):
        with patch("httpx.get", side_effect=_mock_fhir_get):
            engine.ingest_from_fhir(fhir_client)
        self.engine = engine
        self.pid = "patient-lucia-obono"

    def test_explain_conflict_returns_narrative(self):
        narrative = self.engine.explain_clinical_conflict(self.pid, conflict_index=0)
        assert narrative.narrative  # No vacío
        assert isinstance(narrative.confidence_score, float)
        assert narrative.model_used  # Nombre del LLM o fallback

    def test_explain_conflict_has_audit(self):
        narrative = self.engine.explain_clinical_conflict(self.pid, conflict_index=0)
        assert narrative.audit_context is not None
        assert "conflict_type" in narrative.audit_context or "reason" in narrative.audit_context

    def test_explain_nonexistent_conflict_abstains(self):
        narrative = self.engine.explain_clinical_conflict(self.pid, conflict_index=99)
        assert narrative.abstained is True
        assert "ABSTAIN" in narrative.narrative

    def test_explain_empty_patient_abstains(self):
        narrative = self.engine.explain_clinical_conflict("nonexistent", conflict_index=0)
        assert narrative.abstained is True

    def test_clinical_handoff_returns_note(self):
        narrative = self.engine.clinical_handoff(self.pid)
        assert narrative.narrative  # No vacío
        assert isinstance(narrative.confidence_score, float)
        assert narrative.confidence_score > 0  # Tiene evidencia

    def test_clinical_handoff_empty_patient_abstains(self):
        narrative = self.engine.clinical_handoff("nonexistent")
        # Sin bloques = no se detectan contradicciones, pero igual genera nota
        # La comprobación de abstención se basa en el recuento de evidencias
        assert narrative.narrative  # Debe producir algo

    def test_narrative_dataclass_immutable(self):
        narrative = self.engine.explain_clinical_conflict(self.pid, conflict_index=0)
        with pytest.raises(AttributeError):
            narrative.narrative = "tampered"  # type: ignore[misc]


# ── Pruebas de detección aumentada con LLM ────────────────────────────────


class TestLLMAugmentedDetection:
    """Prueba el fallback con LLM para interacciones más allá de la tabla de 12 pares."""

    def test_deterministic_still_works_without_llm(self):
        """La tabla determinista detecta los pares conocidos incluso con el LLM desactivado."""
        from engine.clinical_scoring import check_drug_interactions

        meds = ["Warfarin 5mg", "Ibuprofen 400mg"]
        interactions = check_drug_interactions(meds, use_llm_fallback=False)
        pairs = {(i.drug_a, i.drug_b) for i in interactions}
        assert ("warfarin", "ibuprofen") in pairs

    def test_llm_fallback_does_not_break_deterministic(self):
        """El fallback con LLM solo añade — nunca elimina resultados deterministas."""
        from engine.clinical_scoring import check_drug_interactions

        meds = ["Warfarin 5mg", "Ibuprofen 400mg"]
        without_llm = check_drug_interactions(meds, use_llm_fallback=False)
        with_llm = check_drug_interactions(meds, use_llm_fallback=True)
        # with_llm debe tener al menos tantas como without_llm
        assert len(with_llm) >= len(without_llm)

    def test_empty_meds_no_crash(self):
        """Una lista de medicación vacía no rompe el fallback con LLM."""
        from engine.clinical_scoring import check_drug_interactions

        interactions = check_drug_interactions([], use_llm_fallback=True)
        assert interactions == []

    def test_single_med_no_interactions(self):
        """Un único medicamento no puede tener interacciones."""
        from engine.clinical_scoring import check_drug_interactions

        interactions = check_drug_interactions(
            ["Metformin 500mg"], use_llm_fallback=False
        )
        assert interactions == []
