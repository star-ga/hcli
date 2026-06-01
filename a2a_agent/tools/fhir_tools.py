"""
Herramientas de consulta FHIR — acceso directo a FHIR R4 para el agente Historia Clínica.

Reutiliza el mismo patrón de acceso a FHIR que el proyecto base po-adk-python:
- Las credenciales FHIR provienen de tool_context.state (inyectadas por fhir_hook)
- httpx para las llamadas HTTP
- El manejo de errores devuelve diccionarios estructurados
"""
import logging

import httpx
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_FHIR_TIMEOUT = 15


def _get_fhir_context(tool_context: ToolContext):
    fhir_url = tool_context.state.get("fhir_url", "").rstrip("/")
    fhir_token = tool_context.state.get("fhir_token", "")
    patient_id = tool_context.state.get("patient_id", "")
    missing = [
        name for name, val in [
            ("fhir_url", fhir_url),
            ("fhir_token", fhir_token),
            ("patient_id", patient_id),
        ]
        if not val
    ]
    if missing:
        return {
            "status": "error",
            "error_message": f"Falta el contexto FHIR: {', '.join(missing)}. "
            "Asegúrate de que el llamante incluya 'fhir-context' en los metadatos del mensaje A2A.",
        }
    return fhir_url, fhir_token, patient_id


def _fhir_get(fhir_url: str, token: str, path: str, params: dict | None = None) -> dict:
    resp = httpx.get(
        f"{fhir_url}/{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/fhir+json"},
        timeout=_FHIR_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _coding_display(codings: list) -> str:
    for c in codings:
        if c.get("display"):
            return c["display"]
    return "Unknown"


def get_patient_demographics(tool_context: ToolContext) -> dict:
    """Obtiene los datos demográficos del paciente (nombre, fecha de nacimiento, sexo, contactos) del servidor FHIR."""
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx
    try:
        patient = _fhir_get(fhir_url, fhir_token, f"Patient/{patient_id}")
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    names = patient.get("name", [])
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    given = " ".join(official.get("given", []))
    family = official.get("family", "")
    return {
        "status": "success",
        "patient_id": patient_id,
        "name": f"{given} {family}".strip() or "Unknown",
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
    }


def get_active_medications(tool_context: ToolContext) -> dict:
    """Recupera del servidor FHIR la lista de medicación activa actual del paciente."""
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "MedicationRequest",
            {"patient": patient_id, "status": "active", "_count": "50"},
        )
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    meds = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        concept = res.get("medicationCodeableConcept", {})
        name = concept.get("text") or _coding_display(concept.get("coding", []))
        dosage_list = [d.get("text", "") for d in res.get("dosageInstruction", [])]
        meds.append({
            "medication": name,
            "dosage": dosage_list[0] if dosage_list else "No especificada",
            "authored_on": res.get("authoredOn"),
            "requester": (res.get("requester") or {}).get("display"),
        })
    return {"status": "success", "patient_id": patient_id, "count": len(meds), "medications": meds}


def get_active_conditions(tool_context: ToolContext) -> dict:
    """Recupera del servidor FHIR los problemas de salud activos y los diagnósticos del paciente."""
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Condition",
            {"patient": patient_id, "clinical-status": "active", "_count": "50"},
        )
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    conditions = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        code = res.get("code", {})
        conditions.append({
            "condition": code.get("text") or _coding_display(code.get("coding", [])),
            "severity": (res.get("severity") or {}).get("text"),
            "onset": res.get("onsetDateTime") or (res.get("onsetPeriod") or {}).get("start"),
        })
    return {"status": "success", "patient_id": patient_id, "count": len(conditions), "conditions": conditions}


def get_recent_observations(category: str, tool_context: ToolContext) -> dict:
    """
    Recupera observaciones clínicas recientes (constantes vitales, laboratorio, historia social).

    Args:
        category: Categoría de observación FHIR — 'vital-signs', 'laboratory' o 'social-history'
    """
    ctx = _get_fhir_context(tool_context)
    if isinstance(ctx, dict):
        return ctx
    fhir_url, fhir_token, patient_id = ctx
    category = (category or "vital-signs").strip().lower()
    try:
        bundle = _fhir_get(
            fhir_url, fhir_token, "Observation",
            {"patient": patient_id, "category": category, "_sort": "-date", "_count": "20"},
        )
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    observations = []
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        code = res.get("code", {})
        value, unit = None, None
        if "valueQuantity" in res:
            vq = res["valueQuantity"]
            value, unit = vq.get("value"), vq.get("unit")
        elif "valueString" in res:
            value = res["valueString"]
        observations.append({
            "observation": code.get("text") or _coding_display(code.get("coding", [])),
            "value": value,
            "unit": unit,
            "effective_date": res.get("effectiveDateTime"),
        })
    return {"status": "success", "patient_id": patient_id, "category": category, "observations": observations}
