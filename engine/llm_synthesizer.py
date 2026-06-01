"""
Sintetizador Clínico LLM — generación de narrativa clínica fundamentada en evidencia.

Usa IA generativa para explicar los conflictos detectados en el contexto
clínico específico del paciente, con citas de evidencia explícitas y
abstención rotunda cuando la confianza es baja. Las barreras de seguridad
deterministas (interacciones farmacológicas, comprobaciones de alergias) se
encargan de la detección; el LLM se encarga de la *síntesis y la
explicación*.

Recurre a una salida con plantilla estructurada cuando no hay ninguna clave
de API de LLM disponible.
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClinicalNarrative:
    """Narrativa clínica generada por LLM con citas de evidencia."""

    narrative: str
    evidence_citations: list[dict[str, str]]
    confidence_score: float
    abstained: bool
    model_used: str
    audit_context: dict[str, Any]


# El prompt de sistema ancla el LLM a la evidencia e impone la abstención
_SYSTEM_PROMPT = """Eres un asistente de apoyo a la decisión clínica. DEBES:

1. Usar ÚNICAMENTE la evidencia proporcionada a continuación. Nunca inventes hechos clínicos.
2. CITAR bloques de evidencia específicos por su block_id en cada afirmación que hagas.
3. Si la evidencia es insuficiente para emitir una declaración clínica, responde
   exactamente con: "ABSTAIN: Evidencia insuficiente para ofrecer orientación clínica."
4. Usar lenguaje clínico profesional, apropiado para una nota de relevo asistencial.
5. Incluir la valoración de la severidad y las acciones recomendadas.
6. Nunca emitir un diagnóstico — únicamente señalar problemas de seguridad basados en la evidencia.

Da formato a las citas como [block_id] en línea."""


def _build_conflict_prompt(
    conflict: dict[str, Any],
    patient_context: dict[str, Any],
    evidence_blocks: list[dict[str, Any]],
) -> str:
    """Construye un prompt para explicar un conflicto específico en el contexto del paciente."""
    evidence_text = "\n".join(
        f"- [{b.get('block_id', 'unknown')}] {b.get('title', '')}: {b.get('content', '')}"
        for b in evidence_blocks
    )
    patient_info = (
        f"El paciente tiene {len(patient_context.get('conditions', []))} afecciones, "
        f"{len(patient_context.get('medications', []))} medicamentos, "
        f"{len(patient_context.get('allergies', []))} alergias."
    )
    conditions_text = ", ".join(
        c.get("name", "") for c in patient_context.get("conditions", [])
    )
    if conditions_text:
        patient_info += f"\nAfecciones activas: {conditions_text}"

    return f"""Explica este problema de seguridad clínica en el contexto de este paciente concreto.

CONFLICTO DETECTADO:
- Tipo: {conflict.get('type', 'unknown')}
- Severidad: {conflict.get('severity', 'unknown')}
- Descripción: {conflict.get('description', '')}
- Recomendación actual: {conflict.get('recommendation', '')}

CONTEXTO DEL PACIENTE:
{patient_info}

BLOQUES DE EVIDENCIA:
{evidence_text}

Aporta una justificación clínica de 2-3 frases que explique POR QUÉ esto es peligroso para ESTE paciente concreto, citando los identificadores de los bloques de evidencia. Después, proporciona una recomendación de acción concreta."""


def _build_handoff_prompt(
    patient_context: dict[str, Any],
    contradictions: list[dict[str, Any]],
    safety_report: dict[str, Any],
    evidence_blocks: list[dict[str, Any]],
) -> str:
    """Construye un prompt para generar un resumen completo de relevo asistencial."""
    evidence_text = "\n".join(
        f"- [{b.get('block_id', 'unknown')}] {b.get('title', '')}: {b.get('content', '')}"
        for b in evidence_blocks[:20]  # Limita a los 20 bloques principales
    )
    conflicts_text = "\n".join(
        f"- [{c.get('severity', '?').upper()}] {c.get('description', '')}"
        for c in contradictions
    )
    meds_text = ", ".join(
        m.get("name", "") for m in patient_context.get("medications", [])
    )
    conditions_text = ", ".join(
        c.get("name", "") for c in patient_context.get("conditions", [])
    )

    return f"""Genera una nota de relevo asistencial para este paciente basándote ÚNICAMENTE en la evidencia siguiente.

RESUMEN DEL PACIENTE:
- Medicamentos: {meds_text}
- Afecciones: {conditions_text}
- Alergias: {', '.join(a.get('allergen', '') for a in patient_context.get('allergies', []))}

PROBLEMAS DE SEGURIDAD DETECTADOS ({len(contradictions)} en total):
{conflicts_text or 'Ninguno detectado'}

SEGURIDAD DE LA MEDICACIÓN:
- Interacciones farmacológicas: {safety_report.get('interaction_count', 0)}
- Conflictos por alergia: {safety_report.get('allergy_conflict_count', 0)}

BLOQUES DE EVIDENCIA:
{evidence_text}

Redacta una nota de relevo asistencial estructurada con secciones:
1. ALERTAS CRÍTICAS (si las hay)
2. MEDICAMENTOS ACTIVOS (con señales de seguridad)
3. AFECCIONES ACTIVAS
4. ACCIONES RECOMENDADAS
Cita los bloques de evidencia con [block_id] en cada afirmación clínica."""


async def _call_medical_llm_async(prompt: str, system: str) -> tuple[str | None, str]:
    """Cascada asíncrona de LLM médico: OpenAI GPT-5.4 → MedGemma → Gemini Flash."""
    openai_key = os.environ.get("OPENAI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not openai_key and not google_key:
        return None, "none"

    try:
        import httpx
    except ImportError:
        return None, "none"

    async with httpx.AsyncClient(timeout=8) as client:
        if openai_key:
            try:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    json={
                        "model": "gpt-5.4",
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 1024,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    if text:
                        return text, "OpenAI-GPT-5.4"
                else:
                    logger.info("OpenAI devolvió %d, se intenta el siguiente", resp.status_code)
            except Exception as e:
                logger.info("OpenAI falló: %s, se intenta el siguiente", e)

        if google_key:
            for model_id, model_label in [
                ("medgemma-27b-text-v1", "MedGemma-27B"),
                ("gemini-3-flash", "gemini-3-flash"),
            ]:
                try:
                    resp = await client.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
                        headers={"x-goog-api-key": google_key},
                        json={
                            "systemInstruction": {"parts": [{"text": system}]},
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "temperature": 0.2,
                                "maxOutputTokens": 1024,
                            },
                        },
                    )
                    if resp.status_code != 200:
                        logger.info("%s devolvió %d, se intenta el siguiente", model_label, resp.status_code)
                        continue
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and parts[0].get("text"):
                            return parts[0]["text"], model_label
                except Exception as e:
                    logger.info("%s falló: %s, se intenta el siguiente", model_label, e)
                    continue

    return None, "none"


def _call_medical_llm_sync(prompt: str, system: str) -> tuple[str | None, str]:
    """
    Llama al LLM médico en cascada: OpenAI GPT-5.4 → MedGemma → Gemini Flash.

    Usa las claves de API que estén disponibles. OpenAI cuenta con la
    validación clínica más sólida (260 médicos, acuerdo de protección de
    datos del paciente). MedGemma está diseñado específicamente para
    medicina (87,7 % en MedQA). Gemini Flash es el respaldo general.

    Devuelve (response_text, model_used).
    """
    openai_key = os.environ.get("OPENAI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not openai_key and not google_key:
        return None, "none"

    try:
        import httpx
    except ImportError:
        return None, "none"

    # Intento 1: OpenAI (si hay clave disponible)
    if openai_key:
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {openai_key}"},
                json={
                    "model": "gpt-5.4",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1024,
                },
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                if text:
                    return text, "OpenAI-GPT-5.4"
            else:
                logger.info("OpenAI devolvió %d, se intenta el siguiente modelo", resp.status_code)
        except Exception as e:
            logger.info("OpenAI falló: %s, se intenta el siguiente modelo", e)

    # Intentos 2-3: modelos de Google (MedGemma → Gemini Flash)
    if google_key:
        google_models = [
            ("medgemma-27b-text-v1", "MedGemma-27B"),
            ("gemini-3-flash", "gemini-3-flash"),
        ]
        for model_id, model_label in google_models:
            try:
                resp = httpx.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
                    headers={"x-goog-api-key": google_key},
                    json={
                        "systemInstruction": {"parts": [{"text": system}]},
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "temperature": 0.2,
                            "maxOutputTokens": 1024,
                        },
                    },
                    timeout=8,
                )
                if resp.status_code != 200:
                    logger.info("%s devolvió %d, se intenta el siguiente", model_label, resp.status_code)
                    continue
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and parts[0].get("text"):
                        return parts[0]["text"], model_label
            except Exception as e:
                logger.info("%s falló: %s, se intenta el siguiente", model_label, e)
                continue

    return None, "none"


def _template_conflict_explanation(
    conflict: dict[str, Any],
    patient_context: dict[str, Any],
) -> str:
    """Plantilla de respaldo cuando el LLM no está disponible."""
    conditions = ", ".join(
        c.get("name", "") for c in patient_context.get("conditions", [])
    )
    return (
        f"[{conflict.get('severity', 'unknown').upper()}] {conflict.get('description', '')}. "
        f"Este paciente presenta comorbilidades ({conditions}) que pueden aumentar el riesgo. "
        f"Recomendación: {conflict.get('recommendation', 'Revisar con el prescriptor.')}"
    )


def _template_handoff(
    patient_context: dict[str, Any],
    contradictions: list[dict[str, Any]],
) -> str:
    """Plantilla de relevo de respaldo cuando el LLM no está disponible."""
    lines = ["NOTA DE RELEVO ASISTENCIAL", "=" * 40]

    critical = [c for c in contradictions if c.get("severity") in ("critical", "high")]
    if critical:
        lines.append("\nALERTAS CRÍTICAS:")
        for c in critical:
            lines.append(f"  ! [{c['severity'].upper()}] {c['description']}")
            lines.append(f"    → {c.get('recommendation', 'Revisión requerida')}")

    lines.append("\nMEDICAMENTOS ACTIVOS:")
    for m in patient_context.get("medications", []):
        lines.append(f"  - {m.get('name', 'Desconocido')} ({m.get('dosage', '')})")

    lines.append("\nAFECCIONES ACTIVAS:")
    for c in patient_context.get("conditions", []):
        lines.append(f"  - {c.get('name', 'Desconocida')}")

    lines.append("\nALERGIAS:")
    for a in patient_context.get("allergies", []):
        lines.append(f"  - {a.get('allergen', 'Desconocido')} ({a.get('criticality', '')})")

    return "\n".join(lines)


def explain_conflict(
    conflict: dict[str, Any],
    patient_context: dict[str, Any],
    evidence_blocks: list[dict[str, Any]],
    confidence_threshold: float = 0.3,
) -> ClinicalNarrative:
    """
    Genera una explicación clínica específica para el paciente sobre un conflicto detectado.

    Usa el patrón de detección determinista + síntesis por LLM:
    - La detección se basa en reglas (fiable, auditable)
    - La explicación la genera el LLM (expresiva, sensible al contexto)
    - Abstención rotunda cuando la evidencia es insuficiente
    """
    # Comprueba la confianza — abstenerse si es demasiado baja
    evidence_count = len(evidence_blocks)
    confidence = min(evidence_count / 3.0, 1.0)

    if confidence < confidence_threshold:
        return ClinicalNarrative(
            narrative="ABSTAIN: Evidencia insuficiente para ofrecer orientación clínica. "
            f"Solo hay {evidence_count} bloque(s) de evidencia disponibles; "
            f"el umbral mínimo requiere {int(confidence_threshold * 3)} bloques.",
            evidence_citations=[],
            confidence_score=confidence,
            abstained=True,
            model_used="abstention_gate",
            audit_context={
                "reason": "insufficient_evidence",
                "evidence_count": evidence_count,
                "threshold": confidence_threshold,
            },
        )

    # Construye el prompt
    prompt = _build_conflict_prompt(conflict, patient_context, evidence_blocks)

    # Intenta la síntesis por LLM (cascada MedGemma → Gemini)
    llm_response, model_used = _call_medical_llm_sync(prompt, _SYSTEM_PROMPT)

    if llm_response and "ABSTAIN" not in llm_response:
        # Extrae las citas de la respuesta
        import re

        citations = re.findall(r"\[([^\]]+)\]", llm_response)
        evidence_citations = [
            {"block_id": cid, "role": "supporting_evidence"}
            for cid in citations
            if any(b.get("block_id") == cid for b in evidence_blocks)
        ]
        return ClinicalNarrative(
            narrative=llm_response,
            evidence_citations=evidence_citations,
            confidence_score=confidence,
            abstained=False,
            model_used=model_used,
            audit_context={
                "conflict_type": conflict.get("type"),
                "evidence_count": evidence_count,
                "citations_found": len(evidence_citations),
            },
        )
    elif llm_response and "ABSTAIN" in llm_response:
        return ClinicalNarrative(
            narrative=llm_response,
            evidence_citations=[],
            confidence_score=confidence,
            abstained=True,
            model_used=model_used,
            audit_context={"reason": "llm_abstained", "evidence_count": evidence_count},
        )

    # Recurre a la plantilla
    narrative = _template_conflict_explanation(conflict, patient_context)
    return ClinicalNarrative(
        narrative=narrative,
        evidence_citations=[],
        confidence_score=confidence,
        abstained=False,
        model_used="template_fallback",
        audit_context={"reason": "llm_unavailable", "evidence_count": evidence_count},
    )


def generate_clinical_handoff(
    patient_context: dict[str, Any],
    contradictions: list[dict[str, Any]],
    safety_report: dict[str, Any],
    evidence_blocks: list[dict[str, Any]],
    confidence_threshold: float = 0.3,
) -> ClinicalNarrative:
    """
    Genera una nota completa de relevo asistencial clínico.

    Sintetiza todos los hallazgos detectados en una nota estructurada lista
    para el personal clínico, con citas de evidencia. Se abstiene si la
    evidencia es insuficiente.
    """
    evidence_count = len(evidence_blocks)
    confidence = min(evidence_count / 5.0, 1.0)

    if confidence < confidence_threshold:
        return ClinicalNarrative(
            narrative="ABSTAIN: Datos clínicos insuficientes para un resumen de relevo seguro. "
            f"Solo hay {evidence_count} bloque(s) de evidencia disponibles.",
            evidence_citations=[],
            confidence_score=confidence,
            abstained=True,
            model_used="abstention_gate",
            audit_context={
                "reason": "insufficient_evidence",
                "evidence_count": evidence_count,
            },
        )

    prompt = _build_handoff_prompt(
        patient_context, contradictions, safety_report, evidence_blocks
    )

    llm_response, model_used = _call_medical_llm_sync(prompt, _SYSTEM_PROMPT)

    if llm_response and "ABSTAIN" not in llm_response:
        import re

        citations = re.findall(r"\[([^\]]+)\]", llm_response)
        evidence_citations = [
            {"block_id": cid, "role": "supporting_evidence"}
            for cid in citations
            if any(b.get("block_id") == cid for b in evidence_blocks)
        ]
        return ClinicalNarrative(
            narrative=llm_response,
            evidence_citations=evidence_citations,
            confidence_score=confidence,
            abstained=False,
            model_used=model_used,
            audit_context={
                "contradiction_count": len(contradictions),
                "evidence_count": evidence_count,
                "citations_found": len(evidence_citations),
            },
        )
    elif llm_response and "ABSTAIN" in llm_response:
        return ClinicalNarrative(
            narrative=llm_response,
            evidence_citations=[],
            confidence_score=confidence,
            abstained=True,
            model_used=model_used,
            audit_context={"reason": "llm_abstained"},
        )

    # Recurre a la plantilla
    narrative = _template_handoff(patient_context, contradictions)
    return ClinicalNarrative(
        narrative=narrative,
        evidence_citations=[],
        confidence_score=confidence,
        abstained=False,
        model_used="template_fallback",
        audit_context={"reason": "llm_unavailable", "evidence_count": evidence_count},
    )
