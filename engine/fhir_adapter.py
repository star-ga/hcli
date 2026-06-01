# Copyright 2026 STARGA Inc. — Apache-2.0
"""Adaptador de entrada SMART-on-FHIR R4.

Acepta un Bundle FHIR R4 (collection o transaction) y mapea cada
tipo de recurso reconocido a la representación interna de Historia Clínica.

Tipos de recurso admitidos
--------------------------
Patient · Practitioner · Condition · MedicationStatement ·
AllergyIntolerance · Observation

Modelo de autenticación
-----------------------
El adaptador funciona a nivel de biblioteca.  El transporte, la obtención
del token y la cabecera ``Authorization: Bearer <token>`` son
responsabilidad del llamante (lanzamiento SMART-on-FHIR desde la historia
clínica electrónica o flujo independiente).  El adaptador en sí no realiza
llamadas de red.

Depuración de datos del paciente
--------------------------------
Las entradas Patient.identifier que portan un URI de sistema de número de
historia clínica o de identificador personal se eliminan antes de exponer
cualquier campo en el resultado.  El recuento de tokens redactados se
informa en ``phi_redactions``.

Ancla de auditoría
------------------
``bundle_sha256`` es el SHA-256 de la codificación JSON canónica (claves
ordenadas, sin espacios) del dict de bundle validado.  Cualquier sistema de
auditoría posterior puede recalcular el hash a partir del bundle original
para demostrar que el adaptador procesó exactamente los bytes recibidos.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def _hash_patient_id(patient_id: str) -> str:
    """Prefijo SHA-256 de 16 caracteres de patient_id, seguro para datos del paciente.

    Aplica un hash a patient_id antes de registrarlo mediante `extra={}`.
    Replica `engine/fhir_client.py::_hash_patient_id` y el patrón de
    disciplina de protección de datos del paciente del resto del motor.
    """
    return hashlib.sha256((patient_id or "").encode("utf-8")).hexdigest()[:16]

# ── constantes ──────────────────────────────────────────────────────────────────

_KNOWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "Patient",
        "Practitioner",
        "Condition",
        "MedicationStatement",
        "AllergyIntolerance",
        "Observation",
        "Bundle",
        # tipos contenedores habituales — aceptados pero sin análisis en profundidad
        "Encounter",
        "Procedure",
        "DiagnosticReport",
        "DocumentReference",
        "Device",
        "Organization",
        "Location",
        "Medication",
        "Immunization",
        "CarePlan",
        "Goal",
        "MedicationRequest",
    }
)

# URIs de sistema FHIR que portan identificadores del paciente sujetos a eliminación
_PHI_IDENTIFIER_SYSTEMS: frozenset[str] = frozenset(
    {
        "http://terminology.hl7.org/CodeSystem/v2-0203",  # tipo de codificación de nº de historia / identificador personal
        "urn:oid:2.16.840.1.113883.4.1",                  # OID de identificador personal
        "http://hl7.org/fhir/sid/us-ssn",                 # URI FHIR de identificador personal
        # nº de historia clínica — se elimina cualquier sistema que contenga "mrn" (sin distinguir mayúsculas)
    }
)

_MRN_IDENTIFIER_CODES: frozenset[str] = frozenset({"MR", "MRN", "MRN-ID", "PI", "NH"})

_NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"

# Sistemas FHIR para códigos clínicos
_SNOMED_SYSTEM = "http://snomed.info/sct"
_ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10"
_RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
_LOINC_SYSTEM = "http://loinc.org"


# ── tipos de resultado ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NormalizedCondition:
    """Un recurso Condition reducido a lo esencial de su codificación."""

    resource_id: str
    display_text: str
    snomed_code: str | None
    icd10_code: str | None
    clinical_status: str


@dataclass(frozen=True)
class NormalizedMedication:
    """Un recurso MedicationStatement reducido a lo esencial de su codificación."""

    resource_id: str
    display_text: str
    rxnorm_code: str | None
    status: str
    prescriber_reference: str | None  # p. ej. "Practitioner/prac-cardio"


@dataclass(frozen=True)
class NormalizedAllergy:
    """Un recurso AllergyIntolerance reducido a lo esencial."""

    resource_id: str
    display_text: str
    criticality: str | None   # "high" | "low" | "unable-to-assess" | None
    allergy_type: str | None  # "allergy" | "intolerance"
    category: list[str]       # p. ej. ["medication"]
    snomed_code: str | None


@dataclass(frozen=True)
class NormalizedObservation:
    """Un recurso Observation reducido a lo esencial."""

    resource_id: str
    display_text: str
    loinc_code: str | None
    value: float | str | None    # valor numérico o de cadena
    unit: str | None
    effective_datetime: str | None


@dataclass
class ClinicalIngestResult:
    """Resultado de la ingesta de un Bundle FHIR R4 en Historia Clínica.

    Todos los campos de lista se rellenan a partir del bundle; las listas
    vacías significan que no se encontraron recursos de ese tipo o que todos
    fueron rechazados.

    ``rejected_resources``
        Entradas que el adaptador no pudo mapear, cada una como ``(resource_type, motivo)``.

    ``phi_redactions``
        Recuento de tokens de datos del paciente eliminados de las entradas Patient.identifier.

    ``bundle_sha256``
        SHA-256 de la codificación JSON canónica del bundle validado.
        Actúa como ancla de auditoría para la capa de exportación de auditoría clínica.
    """

    patient_id: str
    normalized_medications: list[str]          # nombres para mostrar usados por check_drug_interactions()
    normalized_conditions: list[NormalizedCondition]
    allergies: list[NormalizedAllergy]
    observations: list[NormalizedObservation]
    practitioner_npis: list[str]
    rejected_resources: list[tuple[str, str]]  # (resource_type, motivo)
    phi_redactions: int
    bundle_sha256: str

    # Vistas estructuradas enriquecidas (conservadas junto a la lista plana de nombres para la puntuación)
    medications: list[NormalizedMedication] = field(default_factory=list)


# ── depuración de datos del paciente ──────────────────────────────────────────

def _is_phi_identifier(identifier: dict) -> bool:
    """Devuelve True si un dict de identificador porta un nº de historia clínica o un identificador personal."""
    system = (identifier.get("system") or "").lower()
    if "ssn" in system or "social-security" in system:
        return True
    if "mrn" in system:
        return True

    # Comprobar los códigos de la codificación de tipo
    type_block = identifier.get("type") or {}
    for coding in type_block.get("coding", []):
        code = (coding.get("code") or "").upper()
        sys_uri = (coding.get("system") or "").lower()
        if code in _MRN_IDENTIFIER_CODES:
            return True
        if "ssn" in sys_uri or "social-security" in sys_uri:
            return True

    # Alternativa: pertenencia por OID/URI de sistema
    for phi_sys in _PHI_IDENTIFIER_SYSTEMS:
        if phi_sys.lower() in system:
            return True

    return False


def _scrub_patient_identifiers(
    identifiers: list[dict],
) -> tuple[list[dict], int]:
    """Elimina los identificadores del paciente; devuelve (lista_limpia, recuento_redacciones)."""
    clean: list[dict] = []
    count = 0
    for ident in identifiers:
        if _is_phi_identifier(ident):
            count += 1
            logger.debug(
                "phi_scrubber: identificador eliminado system=%s",
                ident.get("system", "<desconocido>"),
            )
        else:
            clean.append(ident)
    return clean, count


# ── ayudantes de extracción de códigos ──────────────────────────────────────────

def _first_code_by_system(codings: list[dict], system: str) -> str | None:
    for c in codings:
        if (c.get("system") or "").startswith(system.rstrip("/")):
            return c.get("code") or None
    return None


def _display_text(concept: dict) -> str:
    text = concept.get("text")
    if text:
        return text
    for c in concept.get("coding", []):
        if c.get("display"):
            return c["display"]
    return "Desconocido"


# ── analizadores de recursos ──────────────────────────────────────────────────

def _parse_patient(resource: dict) -> tuple[str, int]:
    """Devuelve (patient_id, phi_redactions) tras depurar los identificadores."""
    patient_id = resource.get("id") or "unknown-patient"
    raw_identifiers = resource.get("identifier") or []
    _, phi_count = _scrub_patient_identifiers(raw_identifiers)
    return patient_id, phi_count


def _parse_condition(resource: dict) -> NormalizedCondition | None:
    code_block = resource.get("code") or {}
    codings = code_block.get("coding") or []
    status_block = resource.get("clinicalStatus") or {}
    status_codings = status_block.get("coding") or []
    clinical_status = (
        status_codings[0].get("code") if status_codings else "unknown"
    )
    return NormalizedCondition(
        resource_id=resource.get("id") or "",
        display_text=_display_text(code_block),
        snomed_code=_first_code_by_system(codings, _SNOMED_SYSTEM),
        icd10_code=_first_code_by_system(codings, _ICD10_SYSTEM),
        clinical_status=clinical_status or "unknown",
    )


def _parse_medication_statement(
    resource: dict,
) -> NormalizedMedication | None:
    concept = resource.get("medicationCodeableConcept") or {}
    codings = concept.get("coding") or []
    info_source = resource.get("informationSource") or {}
    return NormalizedMedication(
        resource_id=resource.get("id") or "",
        display_text=_display_text(concept),
        rxnorm_code=_first_code_by_system(codings, _RXNORM_SYSTEM),
        status=resource.get("status") or "unknown",
        prescriber_reference=info_source.get("reference"),
    )


def _parse_allergy(resource: dict) -> NormalizedAllergy | None:
    code_block = resource.get("code") or {}
    codings = code_block.get("coding") or []
    category_raw = resource.get("category") or []
    return NormalizedAllergy(
        resource_id=resource.get("id") or "",
        display_text=_display_text(code_block),
        criticality=resource.get("criticality"),
        allergy_type=resource.get("type"),
        category=list(category_raw),
        snomed_code=_first_code_by_system(codings, _SNOMED_SYSTEM),
    )


def _parse_observation(resource: dict) -> NormalizedObservation | None:
    code_block = resource.get("code") or {}
    codings = code_block.get("coding") or []

    # Numeric value
    vq = resource.get("valueQuantity") or {}
    if vq:
        value: float | str | None = vq.get("value")
        unit: str | None = vq.get("unit")
    elif "valueString" in resource:
        value = resource["valueString"]
        unit = None
    elif "valueCodeableConcept" in resource:
        concept = resource["valueCodeableConcept"]
        value = _display_text(concept)
        unit = None
    else:
        value = None
        unit = None
        # DEBUG — recurso Observation sin ninguno de los campos value[x]
        # estándar. La especificación FHIR R4 lo permite (una observación
        # PUEDE carecer de valor cuando se usa como cabecera de observaciones
        # de subcomponentes), pero los operadores que auditan la completitud de
        # los datos necesitan visibilidad sobre la frecuencia con que ocurre.
        # Seguro para los datos del paciente: solo el id del recurso
        # (identificador FHIR, sintético en nuestra cohorte) — NUNCA el código
        # LOINC ni el texto para mostrar (narrativa clínica).
        logger.debug(
            "fhir_observation_no_value",
            extra={
                "resource_id": resource.get("id", "?"),
                "loinc_code_present": bool(_first_code_by_system(codings, _LOINC_SYSTEM)),
            },
        )

    return NormalizedObservation(
        resource_id=resource.get("id") or "",
        display_text=_display_text(code_block),
        loinc_code=_first_code_by_system(codings, _LOINC_SYSTEM),
        value=value,
        unit=unit,
        effective_datetime=resource.get("effectiveDateTime"),
    )


def _extract_practitioner_npis(
    resource: dict,
    rejected: list[tuple[str, str]],
) -> list[str]:
    """Extrae y valida con Luhn el identificador de profesional (sistema us-npi) de un recurso Practitioner."""
    from engine.npi_registry import validate_npi

    npis: list[str] = []
    for ident in (resource.get("identifier") or []):
        system = ident.get("system") or ""
        if _NPI_SYSTEM not in system:
            continue
        npi_value = ident.get("value") or ""
        if validate_npi(npi_value):
            npis.append(npi_value)
        else:
            # WARNING — el fallo de validación Luhn del identificador es una
            # señal de calidad de datos de Practitioner que los operadores
            # deben ver en los niveles de log por defecto. Seguro para los datos
            # del paciente: los valores del identificador de profesional son
            # datos de registro público, pero solo registramos los 4 primeros
            # dígitos (el prefijo de organización basta para que los operadores
            # detecten una errata o un patrón de colisión sintética) más el id
            # del recurso (identificador FHIR, sintético en nuestra cohorte).
            # NUNCA registrar el identificador malformado completo — los dígitos
            # parciales no ayudan y podrían filtrar patrones de intento de
            # falsificación en una futura ingesta de historia clínica real.
            logger.warning(
                "fhir_practitioner_npi_luhn_failed",
                extra={
                    "npi_prefix4": (npi_value[:4] if isinstance(npi_value, str) else "?"),
                    "npi_len": (len(npi_value) if isinstance(npi_value, str) else 0),
                    "resource_id": resource.get("id", "?"),
                },
            )
            rejected.append(
                (
                    "Practitioner",
                    f"el identificador de profesional '{npi_value}' no superó la validación Luhn "
                    f"(id de recurso={resource.get('id', '?')})",
                )
            )
    return npis


# ── hash canónico ─────────────────────────────────────────────────────────────

def _canonical_sha256(bundle: dict) -> str:
    """SHA-256 de la codificación JSON canónica (claves ordenadas, sin espacios)."""
    canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── API pública ─────────────────────────────────────────────────────────────────

def ingest_bundle(
    bundle: dict | str,
    *,
    strict_resource_types: bool = False,
) -> ClinicalIngestResult:
    """Ingiere un Bundle FHIR R4 en Historia Clínica.

    Parámetros
    ----------
    bundle
        Un Bundle FHIR R4 como dict de Python o como cadena JSON.
    strict_resource_types
        Cuando es True, los recursos con un ``resourceType`` no reconocido se
        añaden a ``rejected_resources``.  Cuando es False (por defecto) se
        omiten en silencio (modo tolerante para bundles que contienen
        extensiones específicas de perfil).

    Devuelve
    --------
    ClinicalIngestResult
        Resultado poblado; véase el docstring de la clase para la semántica de los campos.

    Lanza
    -----
    ValueError
        Si ``bundle`` no es una estructura de Bundle FHIR válida (falta el array
        ``entry`` o ``resourceType`` es incorrecto).
    """
    # --- parsear la cadena JSON si es necesario --------------------------------
    if isinstance(bundle, str):
        try:
            bundle = json.loads(bundle)
        except json.JSONDecodeError as exc:
            # Log previo al raise seguro para los datos del paciente: solo
            # posición + clase del mensaje, NUNCA la entrada original.
            # JSONDecodeError expone la columna/línea del fallo de parseo, que
            # puede citar contenido del bundle; exc.msg es la frase predefinida
            # ("Expecting value", etc.) — segura.
            logger.error(
                "fhir_bundle_invalid_json",
                extra={
                    "error_type": "JSONDecodeError",
                    "decode_msg": exc.msg,
                    "input_size_bytes": len(bundle) if isinstance(bundle, str) else 0,
                },
            )
            raise ValueError(f"el bundle no es JSON válido: {exc}") from exc

    if not isinstance(bundle, dict):
        logger.error(
            "fhir_bundle_wrong_python_type",
            extra={"actual_type": type(bundle).__name__},
        )
        raise ValueError("el bundle debe ser un dict o una cadena JSON")

    # --- validación estructural ------------------------------------------------
    resource_type = bundle.get("resourceType")
    if resource_type != "Bundle":
        # resourceType es una cadena estándar de FHIR R4 — seguro de registrar
        # de forma categórica. Un bundle con un resourceType muy malformado
        # (p. ej. muy largo, con HTML incrustado) se registra solo por su longitud.
        logger.error(
            "fhir_bundle_wrong_resource_type",
            extra={
                "resource_type_len": (
                    len(resource_type) if isinstance(resource_type, str) else 0
                ),
                "resource_type_class": type(resource_type).__name__,
            },
        )
        raise ValueError(
            f"Se esperaba resourceType='Bundle', se obtuvo '{resource_type}'"
        )
    entries = bundle.get("entry")
    if not isinstance(entries, list):
        logger.error(
            "fhir_bundle_entry_not_array",
            extra={"entry_type_class": type(entries).__name__},
        )
        raise ValueError("Bundle.entry debe ser un array")

    bundle_type = bundle.get("type", "collection")
    if bundle_type not in ("collection", "transaction", "searchset", "batch",
                            "history", "document"):
        logger.error(
            "fhir_bundle_unsupported_type",
            extra={
                "bundle_type_len": (
                    len(bundle_type) if isinstance(bundle_type, str) else 0
                ),
            },
        )
        raise ValueError(f"Bundle.type no admitido: '{bundle_type}'")

    # --- calcular el ancla de auditoría antes de cualquier mutación ------------
    bundle_sha256 = _canonical_sha256(bundle)

    # Log de entrada seguro para los datos del paciente: solo metadatos
    # estructurales — bundle_type es estándar de FHIR R4, el prefijo sha256 es
    # irreversible y entry_count es un recuento.
    logger.debug(
        "fhir_bundle_ingest_start",
        extra={
            "bundle_type": bundle_type,
            "entry_count": len(entries),
            "bundle_sha256_prefix": bundle_sha256[:16],
            "strict_resource_types": strict_resource_types,
        },
    )

    # --- iterar sobre las entradas ---------------------------------------------
    patient_id = "unknown-patient"
    phi_redactions = 0
    conditions: list[NormalizedCondition] = []
    medications: list[NormalizedMedication] = []
    allergies: list[NormalizedAllergy] = []
    observations: list[NormalizedObservation] = []
    practitioner_npis: list[str] = []
    rejected: list[tuple[str, str]] = []

    for entry in entries:
        resource = entry.get("resource") or {}
        rt = resource.get("resourceType") or ""

        if not rt:
            # Cierra la vía de despacho silencioso de entradas sin resourceType.
            # Los operadores que auditan la calidad de los bundles FHIR necesitan
            # una señal por entrada — el resumen agregado
            # `fhir_bundle_rejection_summary` muestra recuentos por resource_type
            # pero agrupa todos los "(unknown)" juntos, ocultando errores en serie
            # del constructor de bundles que emiten muchas entradas con
            # resourceType vacío. Seguro para los datos del paciente: solo
            # metadatos estructurales — sin cuerpo de recurso, sin valores de
            # identificador, sin texto narrativo.
            logger.debug(
                "fhir_bundle_entry_no_resource_type",
                extra={
                    "entry_keys_present": sorted((entry or {}).keys()),
                    "resource_keys_present": sorted((resource or {}).keys()),
                },
            )
            rejected.append(("(unknown)", "entry.resource no tiene resourceType"))
            continue

        if rt not in _KNOWN_RESOURCE_TYPES:
            if strict_resource_types:
                # Nivel WARNING cuando el modo estricto rechaza un recurso — los
                # operadores deben verlo. Los nombres de resourceType son estándar
                # de FHIR R4, configuración pública — seguros de registrar.
                logger.warning(
                    "fhir_bundle_unknown_resource_type_rejected",
                    extra={"resource_type": rt, "strict": True},
                )
                rejected.append((rt, f"resourceType no reconocido '{rt}'"))
            else:
                logger.debug(
                    "fhir_bundle_unknown_resource_type_skipped",
                    extra={"resource_type": rt, "strict": False},
                )
            continue

        try:
            if rt == "Patient":
                pid, phi_count = _parse_patient(resource)
                patient_id = pid
                phi_redactions += phi_count

            elif rt == "Condition":
                cond = _parse_condition(resource)
                if cond:
                    conditions.append(cond)
                else:
                    rejected.append((rt, "el análisis de la afección devolvió None"))

            elif rt == "MedicationStatement":
                med = _parse_medication_statement(resource)
                if med:
                    medications.append(med)
                else:
                    rejected.append((rt, "el análisis del medicamento devolvió None"))

            elif rt == "AllergyIntolerance":
                allergy = _parse_allergy(resource)
                if allergy:
                    allergies.append(allergy)
                else:
                    rejected.append((rt, "el análisis de la alergia devolvió None"))

            elif rt == "Observation":
                obs = _parse_observation(resource)
                if obs:
                    observations.append(obs)
                else:
                    rejected.append((rt, "el análisis de la observación devolvió None"))

            elif rt == "Practitioner":
                npis = _extract_practitioner_npis(resource, rejected)
                practitioner_npis.extend(npis)
                # Cierra la vía de despacho silencioso de Practitioner sin
                # identificador de profesional. _extract_practitioner_npis
                # registra los fallos de Luhn por identificador, pero un
                # Practitioner cuyos identificadores son TODOS de sistemas
                # distintos a us-npi (p. ej. solo ids sintéticos internos)
                # produce en silencio una lista vacía. El flujo de atestación
                # del equipo asistencial depende estrictamente de ≥1 identificador
                # de profesional válido por Practitioner; los operadores necesitan
                # una señal de nivel debug para que una futura ingesta de historia
                # clínica con identificadores de profesional eliminados se detecte
                # de forma visible. Seguro para los datos del paciente: el
                # identificador de profesional es dato de registro público; solo
                # se expone el id del recurso (identificador FHIR, sintético en
                # nuestra cohorte) y el recuento de sistemas de identificador.
                if not npis:
                    logger.debug(
                        "fhir_practitioner_zero_valid_npis",
                        extra={
                            "resource_id": resource.get("id", "?"),
                            "identifier_count": len(
                                resource.get("identifier") or []
                            ),
                            "patient_id_hash_prefix": _hash_patient_id(patient_id),
                        },
                    )

        except Exception as exc:  # noqa: BLE001 — exponer los errores por recurso como rechazos
            # Log de error seguro para los datos del paciente: solo el TIPO de
            # excepción, nunca el cuerpo del mensaje (las excepciones FHIR pueden
            # a veces incrustar contenido del recurso en su mensaje, lo que
            # volvería a filtrar datos).
            logger.warning(
                "fhir_bundle_parse_failure",
                extra={
                    "resource_type": rt,
                    "error_type": type(exc).__name__,
                },
            )
            rejected.append((rt, f"error de análisis: {exc}"))

    # Lista plana de nombres de medicamentos para compatibilidad con check_drug_interactions()
    normalized_medication_names = [m.display_text for m in medications]

    # Log de finalización seguro para los datos del paciente: recuentos +
    # prefijo sha + patient_id (identificador interno tras la depuración).
    # WARNING cuando se rechazó algún recurso; INFO en la vía sin incidencias.
    # Los nombres de fármacos + códigos de afección + valores de observación
    # permanecen sellados — solo se exponen los recuentos.

    # Resumen DEBUG por tipo de recurso rechazado para que los operadores que
    # auditan la deriva de calidad de datos puedan ver QUÉ familias de tipos de
    # recurso están descartando recursos sin necesidad de recorrer la lista
    # completa de rechazos. Seguro para los datos del paciente: solo nombres
    # estructurales de tipo (p. ej. 'Practitioner', 'Condition' — cadenas
    # estándar de FHIR R4).
    if rejected:
        rejection_type_counts: dict[str, int] = {}
        for rt_rej, _reason in rejected:
            rejection_type_counts[rt_rej] = rejection_type_counts.get(rt_rej, 0) + 1
        logger.debug(
            "fhir_bundle_rejection_summary",
            extra={
                "patient_id_hash_prefix": _hash_patient_id(patient_id),
                "total_rejected": len(rejected),
                "by_resource_type": rejection_type_counts,
            },
        )

    log_fn = logger.warning if rejected else logger.info
    log_fn(
        "fhir_bundle_ingest_complete",
        extra={
            "patient_id_hash_prefix": _hash_patient_id(patient_id),
            "bundle_sha256_prefix": bundle_sha256[:16],
            "medication_count": len(medications),
            "condition_count": len(conditions),
            "allergy_count": len(allergies),
            "observation_count": len(observations),
            "practitioner_count": len(practitioner_npis),
            "rejected_count": len(rejected),
            "phi_redactions": phi_redactions,
        },
    )

    return ClinicalIngestResult(
        patient_id=patient_id,
        normalized_medications=normalized_medication_names,
        normalized_conditions=conditions,
        allergies=allergies,
        observations=observations,
        practitioner_npis=practitioner_npis,
        rejected_resources=rejected,
        phi_redactions=phi_redactions,
        bundle_sha256=bundle_sha256,
        medications=medications,
    )


__all__ = [
    "ClinicalIngestResult",
    "NormalizedCondition",
    "NormalizedMedication",
    "NormalizedAllergy",
    "NormalizedObservation",
    "ingest_bundle",
]
