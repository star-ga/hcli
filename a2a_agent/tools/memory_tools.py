"""
Herramientas de memoria clínica — operaciones de memoria persistente para el agente Historia Clínica.

Estas herramientas envuelven el motor de Historia Clínica para ofrecer una recuperación
inteligente del contexto clínico con control de confianza y ponderación por importancia.
"""
import logging
import os
import sys
import time
import uuid

from google.adk.tools import ToolContext

# Garantiza que el motor sea importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from engine.clinical_memory import HistoriaClinicaEngine, ClinicalBlock
from engine.fhir_client import FHIRClient, FHIRContext

logger = logging.getLogger(__name__)

# Instancia compartida del motor
_engine = HistoriaClinicaEngine()

# Modo de prueba: precarga los datos de ejemplo de Lucía Obono Mangue
if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
    try:
        import json as _json

        _fixture_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "tests", "fixtures", "lucia_obono_bundle.json",
        )
        if os.path.exists(_fixture_path):
            with open(_fixture_path) as _f:
                _bundle = _json.load(_f)
            _counts = _engine.ingest_from_bundle(_bundle, "patient-lucia-obono")
            logger.info("MODO DE PRUEBA: precargada Lucía Obono Mangue (%s)", _counts)
    except Exception as e:
        logger.warning("Falló la carga automática de prueba: %s", e)


def _get_engine() -> HistoriaClinicaEngine:
    return _engine


def _auto_ingest(tool_context: ToolContext) -> None:
    """Ingiere automáticamente los datos FHIR si hay credenciales disponibles y no hay datos almacenados."""
    fhir_url = tool_context.state.get("fhir_url", "")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = tool_context.state.get("patient_id", "")
    if not all([fhir_url, fhir_token, patient_id]):
        return
    engine = _get_engine()
    if patient_id in engine._patient_blocks and engine._patient_blocks[patient_id]:
        return
    try:
        ctx = FHIRContext(url=fhir_url, token=fhir_token, patient_id=patient_id)
        fhir = FHIRClient(ctx)
        engine.ingest_from_fhir(fhir)
        logger.info("Datos FHIR ingeridos automáticamente para el paciente %s", patient_id)
    except Exception as e:
        logger.warning("Falló la ingesta automática para %s: %s", patient_id, e)


def _demo_patient_id(tool_context: ToolContext) -> str:
    """Devuelve patient_id del estado de sesión, recurriendo al paciente de prueba si DEMO_MODE."""
    pid = tool_context.state.get("patient_id", "")
    if not pid and os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
        pid = "patient-lucia-obono"
    return pid


def recall_clinical_context(query: str, tool_context: ToolContext, top_k: int = 5) -> dict:
    """
    Recupera la historia clínica relevante del paciente mediante búsqueda híbrida.

    Usa la fusión BM25 + vectorial + RRF con un núcleo de abstención para el
    control de confianza. Devuelve resultados puntuados con una evaluación de
    confianza que indica si hay evidencia suficiente para una respuesta clínica.

    Args:
        query: Pregunta clínica en lenguaje natural (p. ej., "historial de medicación",
               "resultados de laboratorio recientes", "información sobre alergias")
        top_k: Número máximo de resultados a devolver (predeterminado: 10)
    """
    _auto_ingest(tool_context)
    patient_id = _demo_patient_id(tool_context)
    if not patient_id:
        return {"status": "error", "error_message": "No hay patient_id en el contexto de sesión."}

    engine = _get_engine()
    result = engine.recall(patient_id, query, top_k=top_k or 10)

    return {
        "status": "success",
        "patient_id": result.patient_id,
        "query": result.query,
        "confidence": {
            "score": round(result.confidence.score, 3),
            "level": result.confidence.level,
            "should_abstain": result.confidence.should_abstain,
            "reason": result.confidence.reason,
        },
        "results": result.blocks,
        "result_count": len(result.blocks),
        "audit_hash": result.audit_hash,
    }


def store_clinical_note(
    title: str, content: str, observation_type: str, tool_context: ToolContext
) -> dict:
    """
    Almacena una nota clínica u observación en la memoria persistente del paciente.

    Todas las observaciones almacenadas se auditan mediante cadena de hash y quedan
    disponibles para futuras consultas de recuperación.

    Args:
        title: Título breve de la nota clínica
        content: Texto completo de la observación o nota clínica
        observation_type: Tipo de observación — 'clinical_note', 'lab_result',
                         'medication_change', 'assessment', 'plan'
    """
    patient_id = _demo_patient_id(tool_context)
    if not patient_id:
        return {"status": "error", "error_message": "No hay patient_id en el contexto de sesión."}

    engine = _get_engine()
    block = ClinicalBlock(
        block_id=f"note-{uuid.uuid4().hex[:12]}",
        patient_id=patient_id,
        resource_type=observation_type or "clinical_note",
        title=title,
        content=content,
        metadata={"type": observation_type, "source": "agent"},
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        source="Agente Historia Clínica",
    )
    engine._store_block(block)
    audit_hash = engine._append_audit(
        "store_note",
        {"patient_id": patient_id, "title": title, "type": observation_type},
    )
    return {
        "status": "success",
        "block_id": block.block_id,
        "audit_hash": audit_hash,
        "message": f"Se almacenó '{title}' para el paciente {patient_id}",
    }
