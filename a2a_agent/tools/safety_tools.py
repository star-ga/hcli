"""
Herramientas de seguridad clínica — seguridad de la medicación y detección de contradicciones.

Estas herramientas realizan un análisis clínico de varios pasos mediante
núcleos de puntuación.
"""
import logging
import os
import sys

from google.adk.tools import ToolContext

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.clinical_memory import HistoriaClinicaEngine
from engine.fhir_client import FHIRClient, FHIRContext

logger = logging.getLogger(__name__)


def _demo_patient_id(tool_context: ToolContext) -> str:
    """Devuelve patient_id del estado de sesión, recurriendo al paciente de prueba si DEMO_MODE."""
    pid = tool_context.state.get("patient_id", "")
    if not pid and os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
        pid = "patient-lucia-obono"
    return pid


def _get_engine() -> HistoriaClinicaEngine:
    from a2a_agent.tools.memory_tools import _engine
    return _engine


def _auto_ingest(tool_context: ToolContext) -> None:
    fhir_url = tool_context.state.get("fhir_url", "")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = _demo_patient_id(tool_context)
    if not all([fhir_url, fhir_token, patient_id]):
        return
    engine = _get_engine()
    if patient_id in engine._patient_blocks and engine._patient_blocks[patient_id]:
        return
    try:
        ctx = FHIRContext(url=fhir_url, token=fhir_token, patient_id=patient_id)
        fhir = FHIRClient(ctx)
        engine.ingest_from_fhir(fhir)
    except Exception as e:
        logger.warning("Falló la ingesta automática para %s: %s", patient_id, e)


def medication_safety_review(tool_context: ToolContext) -> dict:
    """
    Evaluación integral de la seguridad de la medicación para el paciente actual.

    Realiza la detección de interacciones farmacológicas, la comprobación cruzada de
    alergias y la puntuación de gravedad mediante núcleos de puntuación clínica.
    Devuelve un informe de seguridad estructurado con hallazgos accionables.

    No requiere argumentos — la identidad del paciente proviene del contexto FHIR de la sesión.
    """
    _auto_ingest(tool_context)
    patient_id = _demo_patient_id(tool_context)
    if not patient_id:
        return {"status": "error", "error_message": "No hay patient_id en el contexto de sesión."}

    engine = _get_engine()
    report = engine.medication_safety_check(patient_id)

    interactions = [
        {
            "drug_a": i.drug_a,
            "drug_b": i.drug_b,
            "severity": i.severity,
            "description": i.description,
            "severity_score": i.score,
        }
        for i in report.interactions
    ]

    allergy_conflicts = [
        {
            "allergen": c.allergen,
            "prescribed_medication": c.medication,
            "cross_reaction_group": c.cross_reaction_group,
            "description": c.description,
        }
        for c in report.allergy_conflicts
    ]

    critical_count = sum(
        1 for i in report.interactions if i.severity in ("contraindicated", "serious")
    ) + len(report.allergy_conflicts)

    return {
        "status": "success",
        "patient_id": report.patient_id,
        "medications_reviewed": report.medications,
        "medication_count": len(report.medications),
        "drug_interactions": interactions,
        "interaction_count": len(interactions),
        "allergy_conflicts": allergy_conflicts,
        "allergy_conflict_count": len(allergy_conflicts),
        "critical_findings": critical_count,
        "confidence": {
            "score": round(report.confidence.score, 3),
            "level": report.confidence.level,
        },
        "summary": report.summary,
        "audit_hash": report.audit_hash,
    }


def detect_record_contradictions(tool_context: ToolContext) -> dict:
    """
    Escáner integral de contradicciones clínicas para el paciente actual.

    Detecta 5 tipos de contradicciones:
    1. Conflictos entre alergia y medicación (p. ej., alergia a la penicilina + prescripción de amoxicilina)
    2. Interacciones farmacológicas (p. ej., riesgo de sangrado por warfarina + ibuprofeno)
    3. Contraindicaciones entre laboratorio y medicación (p. ej., FG en descenso + metformina)
    4. Alertas de tendencia de laboratorio (p. ej., trayectoria descendente del FG)
    5. Discrepancias entre profesionales (p. ej., objetivos de tensión arterial contradictorios de distintos especialistas)

    Cada hallazgo incluye el nivel de gravedad y una recomendación clínica accionable.
    No requiere argumentos — la identidad del paciente proviene del contexto FHIR de la sesión.
    """
    _auto_ingest(tool_context)
    patient_id = _demo_patient_id(tool_context)
    if not patient_id:
        return {"status": "error", "error_message": "No hay patient_id en el contexto de sesión."}

    engine = _get_engine()
    contradictions = engine.detect_contradictions(patient_id)

    critical = [c for c in contradictions if c["severity"] == "critical"]
    high = [c for c in contradictions if c["severity"] == "high"]

    escalation = None
    if critical:
        escalation = (
            f"IMMEDIATE — SE REQUIERE REVISIÓN CLÍNICA INMEDIATA. "
            f"{len(critical)} hallazgo(s) crítico(s): "
            + "; ".join(c["description"][:100] for c in critical)
        )
    elif high:
        escalation = (
            f"PRIORITY — SE RECOMIENDA REVISIÓN PRIORITARIA. Se detectaron {len(high)} hallazgo(s) de gravedad alta."
        )

    # Verifica la integridad de la cadena de auditoría como prueba de confianza
    chain_verified = engine.verify_audit_chain()
    trail = engine.get_audit_trail(limit=1)
    latest_audit_hash = trail[-1].get("entry_hash") or trail[-1].get("hash", "") if trail else ""

    return {
        "status": "success",
        "patient_id": patient_id,
        "contradictions": contradictions,
        "contradiction_count": len(contradictions),
        "critical_count": len(critical),
        "high_count": len(high),
        "types_found": list({c["type"] for c in contradictions}),
        "has_critical": bool(critical),
        "has_high": bool(high),
        "escalation": escalation,
        "audit_hash": latest_audit_hash,
        "chain_integrity": "verified" if chain_verified else "TAMPERED",
    }


def explain_clinical_conflict(
    tool_context: ToolContext,
    conflict_index: int = 0,
) -> dict:
    """
    Genera una explicación específica del paciente, elaborada por el LLM, de un conflicto clínico detectado.

    Usa el patrón de detección determinista + síntesis con IA generativa:
    - Detección: barreras de seguridad basadas en reglas (fiables, auditables)
    - Explicación: generada por el LLM (expresiva, sensible al contexto, con citas de evidencia)
    - Abstención: barrera estricta cuando la evidencia es insuficiente — se niega a especular

    Args:
        conflict_index: Qué conflicto detectado explicar (empezando en 0). Ejecuta primero
            detect_record_contradictions para ver los conflictos disponibles.
    """
    _auto_ingest(tool_context)
    patient_id = _demo_patient_id(tool_context)
    if not patient_id:
        return {"status": "error", "error_message": "No hay patient_id en el contexto de sesión."}

    engine = _get_engine()
    narrative = engine.explain_clinical_conflict(patient_id, conflict_index)

    chain_verified = engine.verify_audit_chain()
    trail = engine.get_audit_trail(limit=1)
    latest_audit_hash = trail[-1].get("entry_hash") or trail[-1].get("hash", "") if trail else ""

    return {
        "status": "success",
        "patient_id": patient_id,
        "narrative": narrative.narrative,
        "evidence_citations": narrative.evidence_citations,
        "confidence_score": round(narrative.confidence_score, 3),
        "abstained": narrative.abstained,
        "model_used": narrative.model_used,
        "audit_hash": latest_audit_hash,
        "chain_integrity": "verified" if chain_verified else "TAMPERED",
    }
