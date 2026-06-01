"""
Servidor MCP de Historia Clínica.

Servidor MCP conforme a SHARP-on-MCP que dota a cualquier agente sanitario de IA
de una memoria clínica persistente e inteligente. Proporciona memoria clínica
persistente e inteligente con núcleos de puntuación deterministas.

STARGA Inc. | https://star.ga
"""
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict

from fastmcp import FastMCP

# Garantizar que el motor sea importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.clinical_memory import HistoriaClinicaEngine
from engine.fhir_client import FHIRClient, FHIRContext, FHIRClientError

logger = logging.getLogger(__name__)

# ── Estado global ─────────────────────────────────────────────────────────────

_engine = HistoriaClinicaEngine()

# ── Limitador de tasa (60 solicitudes/minuto por herramienta) ────────────────

_rate_limit_window: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60.0


def _check_rate_limit(tool_name: str) -> None:
    """Lanza ValueError si se supera el límite de tasa."""
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW
    window = [t for t in _rate_limit_window[tool_name] if t > cutoff]
    if len(window) >= _RATE_LIMIT_MAX:
        raise ValueError(f"Límite de tasa superado para {tool_name} (máx. {_RATE_LIMIT_MAX}/min)")
    window.append(now)
    _rate_limit_window[tool_name] = window

# Modo de presentación: precarga los datos de ejemplo de Lucía Obono Mangue
if os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"):
    try:
        _fixture_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tests", "fixtures", "lucia_obono_bundle.json",
        )
        if os.path.exists(_fixture_path):
            with open(_fixture_path) as f:
                _bundle = json.load(f)
            _counts = _engine.ingest_from_bundle(_bundle, "patient-lucia-obono")
            logger.info("MODO PRESENTACIÓN: precargado Lucía Obono Mangue (%s)", _counts)
    except Exception as e:
        logger.warning("Fallo en la carga automática del modo presentación: %s", e)

# ── MCP Server ────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "Historia Clínica",
    instructions=(
        "Memoria clínica persistente, auditable y a prueba de contradicciones. "
        "Proporciona análisis inteligente de seguridad de la medicación, recuperación "
        "del contexto clínico y registros de auditoría inalterables para agentes "
        "sanitarios de IA. Memoria clínica persistente con núcleos de puntuación "
        "deterministas."
    ),
)


def _get_fhir_context_from_headers(headers: dict[str, str] | None) -> FHIRContext | None:
    """Extrae el contexto FHIR de las cabeceras HTTP de SHARP-on-MCP."""
    if not headers:
        return None
    url = headers.get("x-fhir-server-url", "") or headers.get("X-FHIR-Server-URL", "")
    token = headers.get("x-fhir-access-token", "") or headers.get("X-FHIR-Access-Token", "")
    patient = headers.get("x-patient-id", "") or headers.get("X-Patient-ID", "")
    if url and token and patient:
        return FHIRContext(url=url, token=token, patient_id=patient)
    return None


# ── Herramienta: Almacenar observación clínica ───────────────────────────────

@mcp.tool()
def store_clinical_observation(
    patient_id: str,
    observation_type: str,
    title: str,
    content: str,
    source: str = "manual",
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Almacena una observación o nota clínica de un paciente en la memoria persistente.

    Args:
        patient_id: El ID FHIR del paciente
        observation_type: Tipo de observación (p. ej., 'clinical_note', 'lab_result', 'medication_change')
        title: Título breve de la observación
        content: Texto completo de la observación clínica
        source: Quién o qué creó esta observación
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("store_clinical_observation")
    from engine.clinical_memory import ClinicalBlock

    block = ClinicalBlock(
        block_id=f"obs-{uuid.uuid4().hex[:12]}",
        patient_id=patient_id,
        resource_type=observation_type,
        title=title,
        content=content,
        metadata={"type": observation_type, "source": source},
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        source=source,
    )
    _engine._store_block(block)
    audit_hash = _engine._append_audit(
        "store_observation",
        {"patient_id": patient_id, "title": title, "type": observation_type},
    )
    return {
        "status": "success",
        "block_id": block.block_id,
        "audit_hash": audit_hash,
        "message": f"Observación '{title}' almacenada para el paciente {patient_id}",
    }


# ── Herramienta: Recuperar contexto del paciente ─────────────────────────────

@mcp.tool()
def recall_patient_context(
    patient_id: str,
    query: str,
    top_k: int = 10,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Recupera el historial relevante del paciente mediante consulta usando búsqueda híbrida (BM25 + vector + RRF).

    Utiliza un núcleo de abstención para el control de confianza: indicará cuándo
    la evidencia es insuficiente para una respuesta clínica segura.

    Args:
        patient_id: El ID FHIR del paciente
        query: Consulta clínica en lenguaje natural (p. ej., "¿Qué medicamentos toma este paciente?")
        top_k: Número máximo de resultados a devolver
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("recall_patient_context")
    # Ingesta automática desde FHIR si hay contexto y no hay bloques almacenados
    if fhir_server_url and fhir_access_token:
        if patient_id not in _engine._patient_blocks or not _engine._patient_blocks[patient_id]:
            try:
                ctx = FHIRContext(url=fhir_server_url, token=fhir_access_token, patient_id=patient_id)
                fhir = FHIRClient(ctx)
                _engine.ingest_from_fhir(fhir)
            except Exception as e:
                logger.warning("Fallo en la ingesta automática: %s", e)

    result = _engine.recall(patient_id, query, top_k=top_k)
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


# ── Herramienta: Comprobar conflictos de medicación ──────────────────────────

@mcp.tool()
def check_medication_conflicts(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Detecta contradicciones por interacción farmacológica en la medicación activa de un paciente.

    Utiliza un núcleo adversarial para el análisis con detección de negaciones y
    puntuación clínica para clasificar la gravedad.

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("check_medication_conflicts")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    report = _engine.medication_safety_check(patient_id)

    # Recomendaciones de actuación clínica según la gravedad
    recommendations = []
    for i in report.interactions:
        if i.severity == "contraindicated":
            recommendations.append(f"SUSPENDER: {i.drug_a} + {i.drug_b} está contraindicado. {i.description}.")
        elif i.severity == "serious":
            recommendations.append(f"REVISAR: {i.drug_a} + {i.drug_b} — {i.description}. Considere alternativas.")
    for c in report.allergy_conflicts:
        recommendations.append(
            f"SUSPENDER: {c.medication} prescrito a pesar de la alergia a {c.allergen} ({c.description}). "
            f"Use una alternativa fuera de la clase {c.cross_reaction_group}."
        )

    return {
        "status": "success",
        "patient_id": report.patient_id,
        "medications": report.medications,
        "interactions": [
            {
                "drug_a": i.drug_a,
                "drug_b": i.drug_b,
                "severity": i.severity,
                "description": i.description,
                "score": i.score,
            }
            for i in report.interactions
        ],
        "interaction_count": len(report.interactions),
        "allergy_conflicts": [
            {
                "allergen": c.allergen,
                "medication": c.medication,
                "cross_reaction_group": c.cross_reaction_group,
                "description": c.description,
            }
            for c in report.allergy_conflicts
        ],
        "allergy_conflict_count": len(report.allergy_conflicts),
        "recommendations": recommendations,
        "confidence": {
            "score": round(report.confidence.score, 3),
            "level": report.confidence.level,
        },
        "summary": report.summary,
        "audit_hash": report.audit_hash,
    }


# ── Herramienta: Comprobar conflictos por alergia ────────────────────────────

@mcp.tool()
def check_allergy_conflicts(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Contrasta los medicamentos prescritos con las alergias conocidas del paciente.

    Detecta reactividad cruzada (p. ej., alergia a la penicilina frente a una prescripción de amoxicilina).

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("check_allergy_conflicts")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    report = _engine.medication_safety_check(patient_id)
    return {
        "status": "success",
        "patient_id": report.patient_id,
        "allergy_conflicts": [
            {
                "allergen": c.allergen,
                "medication": c.medication,
                "cross_reaction_group": c.cross_reaction_group,
                "description": c.description,
            }
            for c in report.allergy_conflicts
        ],
        "conflict_count": len(report.allergy_conflicts),
        "audit_hash": report.audit_hash,
    }


# ── Herramienta: Obtener dependencias de tratamiento ─────────────────────────

@mcp.tool()
def get_treatment_dependencies(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Muestra la cadena causal de las decisiones de tratamiento y sus dependencias.

    Relaciona afecciones con medicamentos y con observaciones, mostrando por qué
    se prescribió cada tratamiento y qué depende de él.

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("get_treatment_dependencies")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    blocks = _engine._patient_blocks.get(patient_id, [])

    conditions = [b for b in blocks if b.resource_type == "Condition"]
    medications = [b for b in blocks if b.resource_type == "MedicationRequest"]

    # Construir un mapa simple de dependencias: afección -> medicamentos
    dependencies = []
    for cond in conditions:
        cond_name = cond.metadata.get("condition_name", "").lower()
        related_meds = []
        for med in medications:
            med_name = med.metadata.get("medication_name", "").lower()
            # Heurística simple: asociaciones comunes entre afección y medicamento
            if _is_related(cond_name, med_name):
                related_meds.append(med.metadata.get("medication_name"))
        dependencies.append({
            "condition": cond.metadata.get("condition_name"),
            "severity": cond.metadata.get("severity"),
            "onset": cond.metadata.get("onset"),
            "related_medications": related_meds,
        })

    audit_hash = _engine._append_audit(
        "treatment_dependencies",
        {"patient_id": patient_id, "dependency_count": len(dependencies)},
    )
    return {
        "status": "success",
        "patient_id": patient_id,
        "dependencies": dependencies,
        "audit_hash": audit_hash,
    }


# ── Herramienta: Registro de auditoría clínica ───────────────────────────────

@mcp.tool()
def get_clinical_audit_trail(limit: int = 50) -> dict:
    """
    Recupera el registro de auditoría inalterable, encadenado por hash, de todas las decisiones clínicas.

    Cada entrada se encadena mediante SHA-256 con la anterior, lo que proporciona
    detección de manipulaciones de grado clínico para el registro de decisiones.

    Args:
        limit: Número máximo de entradas de auditoría a devolver (las más recientes primero)
    """
    _check_rate_limit("get_clinical_audit_trail")
    trail = _engine.get_audit_trail(limit)
    chain_valid = _engine.verify_audit_chain()
    return {
        "status": "success",
        "chain_integrity": "verified" if chain_valid else "TAMPERED",
        "entry_count": len(trail),
        "entries": trail[-limit:],
    }


# ── Herramienta: Resumir historial del paciente ──────────────────────────────

@mcp.tool()
def summarize_patient_history(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Genera una visión condensada del paciente con relevancia puntuada por importancia.

    Utiliza un núcleo de importancia para priorizar las afecciones agudas sobre
    las históricas, y las observaciones recientes sobre las más antiguas.

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("summarize_patient_history")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    summary = _engine.patient_summary(patient_id)
    return {"status": "success", **summary}


# ── Herramienta: Detectar deriva de creencias ────────────────────────────────

@mcp.tool()
def detect_belief_drift(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Escáner integral de contradicciones clínicas.

    Detecta 5 tipos de contradicciones:
    1. Conflictos alergia-medicación (p. ej., alergia a la penicilina + prescripción de amoxicilina)
    2. Interacciones farmacológicas (p. ej., riesgo de sangrado por warfarina + AINE)
    3. Contraindicaciones laboratorio-medicación (p. ej., descenso del FG + metformina)
    4. Alertas de tendencia analítica (p. ej., trayectoria descendente del FG acercándose al umbral de peligro)
    5. Discrepancias entre facultativos (p. ej., objetivos de tensión arterial contradictorios de distintos especialistas)

    Cada hallazgo incluye el nivel de gravedad y una recomendación clínica accionable.

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("detect_belief_drift")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    contradictions = _engine.detect_contradictions(patient_id)

    critical = [c for c in contradictions if c["severity"] == "critical"]
    high = [c for c in contradictions if c["severity"] == "high"]

    escalation = None
    if critical:
        escalation = (
            "SE REQUIERE REVISIÓN CLÍNICA INMEDIATA. "
            f"Se han detectado {len(critical)} hallazgo(s) crítico(s) que pueden suponer un riesgo inminente para la seguridad del paciente. "
            "Se recomienda verificación con supervisión humana antes de cualquier actuación clínica."
        )
    elif high:
        escalation = (
            f"SE RECOMIENDA REVISIÓN PRIORITARIA. Se han detectado {len(high)} hallazgo(s) de gravedad alta. "
            "Programe una revisión por un facultativo en un plazo de 24-48 horas."
        )

    return {
        "status": "success",
        "patient_id": patient_id,
        "contradictions": contradictions,
        "contradiction_count": len(contradictions),
        "critical_count": len(critical),
        "high_count": len(high),
        "types_found": list({c["type"] for c in contradictions}),
        "has_critical": bool(critical),
        "escalation": escalation,
    }


# ── Herramienta: Ingerir datos FHIR ──────────────────────────────────────────

@mcp.tool()
def ingest_patient_data(
    patient_id: str,
    fhir_server_url: str,
    fhir_access_token: str,
) -> dict:
    """
    Extrae y almacena en la memoria clínica todos los datos FHIR disponibles de un paciente.

    Ingiere medicamentos, afecciones, alergias, constantes vitales y resultados de laboratorio.
    Esto crea la base de memoria sobre la que consultan todas las demás herramientas.

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: La URL del servidor FHIR R4
        fhir_access_token: Token Bearer para la autenticación del servidor FHIR
    """
    _check_rate_limit("ingest_patient_data")
    try:
        ctx = FHIRContext(url=fhir_server_url, token=fhir_access_token, patient_id=patient_id)
        fhir = FHIRClient(ctx)
        counts = _engine.ingest_from_fhir(fhir)
        total = sum(counts.values())
        return {
            "status": "success",
            "patient_id": patient_id,
            "ingested": counts,
            "total_blocks": total,
            "message": f"Se ingirieron {total} registros clínicos para el paciente {patient_id}",
        }
    except FHIRClientError as e:
        return {"status": "error", "error_message": str(e)}
    except Exception as e:
        return {"status": "error", "error_message": f"La ingesta ha fallado: {e}"}


# ── Herramienta: Explicar conflicto clínico (síntesis con IA generativa) ──────

@mcp.tool()
def explain_clinical_conflict(
    patient_id: str,
    conflict_index: int = 0,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Genera una explicación con LLM, específica del paciente, para un conflicto clínico detectado.

    Utiliza el patrón de detección determinista + síntesis con IA generativa:
    - Detección: salvaguardas basadas en reglas (fiables, auditables)
    - Explicación: generada por LLM (expresiva, sensible al contexto, con citas de evidencia)
    - Abstención: barrera estricta cuando la evidencia es insuficiente — se niega a adivinar

    Patrón: la seguridad determinista detecta el problema, la IA generativa lo explica en su contexto.

    Args:
        patient_id: El ID FHIR del paciente
        conflict_index: Qué conflicto detectado explicar (índice empezando en 0)
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("explain_clinical_conflict")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    narrative = _engine.explain_clinical_conflict(patient_id, conflict_index)
    return {
        "status": "success",
        "patient_id": patient_id,
        "narrative": narrative.narrative,
        "evidence_citations": narrative.evidence_citations,
        "confidence_score": round(narrative.confidence_score, 3),
        "abstained": narrative.abstained,
        "model_used": narrative.model_used,
    }


# ── Herramienta: Nota de transferencia asistencial (síntesis con IA generativa) ──

@mcp.tool()
def clinical_care_handoff(
    patient_id: str,
    fhir_server_url: str = "",
    fhir_access_token: str = "",
) -> dict:
    """
    Genera una nota completa de transferencia asistencial usando síntesis con IA generativa.

    Combina todos los hallazgos de seguridad detectados en una nota estructurada y
    lista para el facultativo, con citas de evidencia. Utiliza un LLM para sintetizar
    los hallazgos en lenguaje clínico natural manteniendo la trazabilidad hasta los
    registros de origen.

    Demuestra:
    - Generación fundamentada en evidencia (cada afirmación cita un bloque de origen)
    - Abstención segura (se niega cuando los datos son insuficientes)
    - Patrón de detección determinista + explicación con IA generativa

    Args:
        patient_id: El ID FHIR del paciente
        fhir_server_url: URL del servidor FHIR (de las cabeceras SHARP-on-MCP)
        fhir_access_token: Token de acceso FHIR (de las cabeceras SHARP-on-MCP)
    """
    _check_rate_limit("clinical_care_handoff")
    _auto_ingest(patient_id, fhir_server_url, fhir_access_token)
    narrative = _engine.clinical_handoff(patient_id)
    return {
        "status": "success",
        "patient_id": patient_id,
        "handoff_note": narrative.narrative,
        "evidence_citations": narrative.evidence_citations,
        "confidence_score": round(narrative.confidence_score, 3),
        "abstained": narrative.abstained,
        "model_used": narrative.model_used,
    }


# ── Funciones auxiliares ──────────────────────────────────────────────────────

def _auto_ingest(patient_id: str, fhir_url: str, fhir_token: str) -> None:
    """Ingesta automática desde FHIR si hay credenciales y no hay datos almacenados."""
    if not fhir_url or not fhir_token:
        return
    if patient_id in _engine._patient_blocks and _engine._patient_blocks[patient_id]:
        return
    try:
        ctx = FHIRContext(url=fhir_url, token=fhir_token, patient_id=patient_id)
        fhir = FHIRClient(ctx)
        _engine.ingest_from_fhir(fhir)
    except Exception as e:
        logger.warning("Fallo en la ingesta automática para %s: %s", patient_id, e)


# Asociaciones comunes entre afección y medicamento para el mapeo de dependencias
_CONDITION_MED_MAP: dict[str, list[str]] = {
    "diabetes": ["metformin", "insulin", "glipizide", "sitagliptin"],
    "hypertension": ["lisinopril", "amlodipine", "losartan", "metoprolol", "hydrochlorothiazide"],
    "atrial fibrillation": ["warfarin", "apixaban", "rivaroxaban", "metoprolol", "diltiazem"],
    "chronic kidney": ["lisinopril", "losartan", "furosemide"],
    "hyperlipidemia": ["atorvastatin", "simvastatin", "rosuvastatin"],
    "heart failure": ["metoprolol", "lisinopril", "furosemide", "spironolactone"],
    "depression": ["fluoxetine", "sertraline", "escitalopram"],
    "anxiety": ["sertraline", "buspirone", "escitalopram"],
    "pain": ["acetaminophen", "ibuprofen", "naproxen", "tramadol"],
    "infection": ["amoxicillin", "azithromycin", "ciprofloxacin", "doxycycline"],
}


def _is_related(condition: str, medication: str) -> bool:
    """Comprueba si una afección y un medicamento están comúnmente asociados."""
    for cond_key, med_list in _CONDITION_MED_MAP.items():
        if cond_key in condition:
            if any(m in medication for m in med_list):
                return True
    return False


# ── Comprobación de estado ──────────────────────────────────────────────────

@mcp.tool()
def health_check() -> dict:
    """
    Punto de comprobación de estado para orquestadores de contenedores (Azure, K8s).

    Devuelve el estado del servidor, el tiempo de actividad y la disponibilidad del motor.
    """
    return {
        "status": "healthy",
        "engine_ready": True,
        "backend_available": _engine._backend_available,
        "audit_chain_active": _engine._audit_chain_mm is not None,
    }


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    logger.info("Iniciando el servidor MCP de Historia Clínica en el puerto %d", port)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
