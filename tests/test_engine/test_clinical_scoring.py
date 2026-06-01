"""Pruebas de las funciones de puntuación clínica."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.clinical_scoring import (
    ClinicalConfidence,
    DrugInteraction,
    AllergyConflict,
    LabMedContraindication,
    LabTrend,
    ProviderDisagreement,
    confidence_gate,
    clinical_importance,
    medication_severity_score,
    is_negation_query,
    check_drug_interactions,
    check_allergy_conflicts,
    check_lab_medication_contraindications,
    detect_lab_trends,
    detect_provider_disagreements,
)


# ── Control de confianza (núcleo de abstención) ───────────────────────────────

class TestConfidenceGate:
    def test_empty_scores_abstains(self):
        result = confidence_gate([], [])
        assert result.should_abstain is True
        assert result.score == 0.0
        assert result.level == "low"

    def test_high_scores_does_not_abstain(self):
        result = confidence_gate([0.9, 0.8, 0.7], [0.9, 0.8, 0.7])
        assert result.should_abstain is False
        assert result.score > 0.3
        assert result.level in ("moderate", "high")

    def test_low_scores_abstains(self):
        result = confidence_gate([0.01], [0.01])
        assert result.should_abstain is True
        assert result.score < 0.3

    def test_custom_threshold(self):
        result = confidence_gate([0.5], [0.5], abstention_threshold=0.9)
        assert result.should_abstain is True

    def test_confidence_level_high(self):
        result = confidence_gate([1.0, 1.0], [1.0, 1.0])
        assert result.level == "high"

    def test_confidence_returns_reason(self):
        result = confidence_gate([0.5], [0.5])
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0


# ── Puntuación de importancia clínica (núcleo de importancia) ─────────────────

class TestClinicalImportance:
    def test_returns_float_in_range(self):
        score = clinical_importance(
            access_count=5, days_since_access=1.0, connection_degree=3
        )
        assert 0.8 <= score <= 1.5

    def test_acute_condition_boost(self):
        normal = clinical_importance(
            access_count=5, days_since_access=1.0, connection_degree=3, is_acute=False
        )
        acute = clinical_importance(
            access_count=5, days_since_access=1.0, connection_degree=3, is_acute=True
        )
        assert acute >= normal

    def test_recent_higher_than_old(self):
        recent = clinical_importance(
            access_count=1, days_since_access=0.0, connection_degree=1
        )
        old = clinical_importance(
            access_count=1, days_since_access=365.0, connection_degree=1
        )
        assert recent > old

    def test_more_connections_higher(self):
        low = clinical_importance(
            access_count=1, days_since_access=1.0, connection_degree=0
        )
        high = clinical_importance(
            access_count=1, days_since_access=1.0, connection_degree=10
        )
        assert high > low


# ── Puntuación de gravedad de medicamentos ────────────────────────────────────

class TestMedicationSeverity:
    def test_contraindicated_is_highest(self):
        assert medication_severity_score("contraindicated") == 1.0

    def test_minor_is_lowest(self):
        assert medication_severity_score("minor") == 0.2

    def test_severity_boost(self):
        base = medication_severity_score("moderate")
        boosted = medication_severity_score("moderate", severity="high")
        assert boosted > base

    def test_unknown_has_default(self):
        score = medication_severity_score("unknown")
        assert 0.0 < score < 1.0


# ── Detección de negación (núcleo adversario) ─────────────────────────────────

class TestNegationDetection:
    def test_detects_not(self):
        assert is_negation_query("Patient is NOT allergic to penicillin") is True

    def test_detects_denies(self):
        assert is_negation_query("Patient denies chest pain") is True

    def test_detects_negative_for(self):
        assert is_negation_query("Negative for COVID-19") is True

    def test_detects_ruled_out(self):
        assert is_negation_query("Heart failure ruled out") is True

    def test_no_negation(self):
        assert is_negation_query("Patient has diabetes") is False

    def test_detects_absence(self):
        assert is_negation_query("Absence of fever") is True


# ── Comprobación de interacciones farmacológicas ──────────────────────────────

class TestDrugInteractions:
    def test_warfarin_aspirin_interaction(self):
        interactions = check_drug_interactions(["Warfarin", "Aspirin"])
        assert len(interactions) >= 1
        assert any(i.drug_a == "warfarin" and i.drug_b == "aspirin" for i in interactions)

    def test_no_interactions_for_safe_combo(self):
        interactions = check_drug_interactions(["Acetaminophen", "Vitamin D"])
        assert len(interactions) == 0

    def test_severity_sorting(self):
        interactions = check_drug_interactions(
            ["Warfarin", "Aspirin", "Lisinopril", "Potassium"]
        )
        if len(interactions) >= 2:
            assert interactions[0].score >= interactions[-1].score

    def test_empty_list(self):
        interactions = check_drug_interactions([])
        assert interactions == []

    def test_warfarin_nsaid_interaction(self):
        interactions = check_drug_interactions(["Warfarin", "Ibuprofen"])
        assert len(interactions) >= 1
        assert any("hemorragia" in i.description.lower() for i in interactions)


# ── Comprobación de conflictos por alergia ────────────────────────────────────

class TestAllergyConflicts:
    def test_penicillin_amoxicillin_conflict(self):
        conflicts = check_allergy_conflicts(["Penicillin"], ["Amoxicillin"])
        assert len(conflicts) >= 1
        assert any(c.allergen == "penicillin" and c.medication == "amoxicillin" for c in conflicts)

    def test_no_conflict_for_safe_combo(self):
        conflicts = check_allergy_conflicts(["Penicillin"], ["Metformin"])
        assert len(conflicts) == 0

    def test_sulfa_cross_reaction(self):
        conflicts = check_allergy_conflicts(["Sulfa"], ["Sulfamethoxazole"])
        assert len(conflicts) >= 1

    def test_empty_allergies(self):
        conflicts = check_allergy_conflicts([], ["Amoxicillin"])
        assert conflicts == []

    def test_empty_medications(self):
        conflicts = check_allergy_conflicts(["Penicillin"], [])
        assert conflicts == []


# ── Comprobación de contraindicaciones analítica-medicamento ──────────────────


class TestLabMedContraindications:
    def test_low_gfr_metformin_critical(self):
        obs = [{"observation_name": "eGFR", "value": 28, "unit": "mL/min/1.73m2"}]
        results = check_lab_medication_contraindications(obs, ["Metformin 500mg"])
        assert len(results) >= 1
        critical = [r for r in results if r.severity == "critical"]
        assert len(critical) >= 1
        assert "metformin" in critical[0].medication.lower()

    def test_moderate_gfr_metformin_high(self):
        obs = [{"observation_name": "eGFR", "value": 38, "unit": "mL/min/1.73m2"}]
        results = check_lab_medication_contraindications(obs, ["Metformin 500mg"])
        assert len(results) >= 1
        assert any(r.severity == "high" for r in results)

    def test_normal_gfr_metformin_no_alert(self):
        obs = [{"observation_name": "eGFR", "value": 65, "unit": "mL/min/1.73m2"}]
        results = check_lab_medication_contraindications(obs, ["Metformin 500mg"])
        assert len(results) == 0

    def test_high_inr_warfarin(self):
        obs = [{"observation_name": "INR", "value": 3.8, "unit": "{INR}"}]
        results = check_lab_medication_contraindications(obs, ["Warfarin 5mg"])
        assert len(results) >= 1
        assert any("warfarin" in r.medication.lower() for r in results)

    def test_no_matching_medication(self):
        obs = [{"observation_name": "eGFR", "value": 25, "unit": "mL/min/1.73m2"}]
        results = check_lab_medication_contraindications(obs, ["Atorvastatin"])
        assert len(results) == 0

    def test_returns_recommendation(self):
        obs = [{"observation_name": "eGFR", "value": 28, "unit": "mL/min/1.73m2"}]
        results = check_lab_medication_contraindications(obs, ["Metformin 500mg"])
        assert len(results) >= 1
        assert len(results[0].recommendation) > 0


# ── Detección de tendencias analíticas ────────────────────────────────────────


class TestLabTrends:
    def test_declining_gfr_detected(self):
        obs = [
            {"observation_name": "eGFR", "value": 45, "effective_date": "2025-09-15"},
            {"observation_name": "eGFR", "value": 38, "effective_date": "2025-12-10"},
            {"observation_name": "eGFR", "value": 32, "effective_date": "2026-02-28"},
        ]
        trends = detect_lab_trends(obs)
        assert len(trends) >= 1
        assert trends[0].direction == "declining"
        assert "45" in trends[0].description
        assert "32" in trends[0].description

    def test_stable_gfr_no_trend(self):
        obs = [
            {"observation_name": "eGFR", "value": 60, "effective_date": "2025-09-15"},
            {"observation_name": "eGFR", "value": 59, "effective_date": "2025-12-10"},
        ]
        trends = detect_lab_trends(obs)
        assert len(trends) == 0

    def test_single_value_no_trend(self):
        obs = [{"observation_name": "eGFR", "value": 30, "effective_date": "2026-02-28"}]
        trends = detect_lab_trends(obs)
        assert len(trends) == 0

    def test_trend_has_recommendation(self):
        obs = [
            {"observation_name": "eGFR", "value": 45, "effective_date": "2025-09-15"},
            {"observation_name": "eGFR", "value": 32, "effective_date": "2026-02-28"},
        ]
        trends = detect_lab_trends(obs)
        assert len(trends) >= 1
        assert len(trends[0].recommendation) > 0


# ── Detección de discrepancias entre profesionales ────────────────────────────


class TestProviderDisagreements:
    def test_bp_target_conflict_detected(self):
        blocks = [
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Blood Pressure: 148 mmHg. Target: <130/80 per cardiologist",
                "source": "Dr. Maria Lopez (Cardiologist)",
                "metadata": {"notes": ""},
            },
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Blood Pressure: 142 mmHg. Target: <140/90 per nephrologist",
                "source": "Dr. Raj Patel (Nephrologist)",
                "metadata": {"notes": ""},
            },
        ]
        disagreements = detect_provider_disagreements(blocks)
        assert len(disagreements) >= 1
        assert disagreements[0].topic == "Objetivo de tensión arterial"
        assert "130" in disagreements[0].provider_a_position
        assert "140" in disagreements[0].provider_b_position

    def test_same_target_no_conflict(self):
        blocks = [
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Blood Pressure: 148 mmHg. Target: <130/80 per cardiologist",
                "source": "Dr. A",
                "metadata": {"notes": ""},
            },
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Blood Pressure: 142 mmHg. Target: <130/80 per other doc",
                "source": "Dr. B",
                "metadata": {"notes": ""},
            },
        ]
        disagreements = detect_provider_disagreements(blocks)
        assert len(disagreements) == 0

    def test_disagreement_has_recommendation(self):
        blocks = [
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Target: <130/80",
                "source": "Dr. A",
                "metadata": {"notes": ""},
            },
            {
                "title": "vital-signs: Blood Pressure",
                "content": "Target: <140/90",
                "source": "Dr. B",
                "metadata": {"notes": ""},
            },
        ]
        disagreements = detect_provider_disagreements(blocks)
        assert len(disagreements) >= 1
        assert "coordinación" in disagreements[0].recommendation.lower()
