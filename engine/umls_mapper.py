"""Metatesauro UMLS — mapeo de conceptos entre vocabularios.

Mapea entre ICD-10, SNOMED CT, LOINC, RxNorm y MeSH mediante
el endpoint de crosswalk de la API REST UMLS de la NLM.

Requiere la variable de entorno UMLS_API_KEY.
Documentación de la API: https://documentation.uts.nlm.nih.gov/rest/home.html
"""
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

UMLS_API_KEY = os.environ.get("UMLS_API_KEY", "")
UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"

_TIMEOUT = 10

# Abreviaturas de origen (source abbreviations)
SAB_SNOMEDCT = "SNOMEDCT_US"
SAB_ICD10CM = "ICD10CM"
SAB_LOINC = "LNC"
SAB_RXNORM = "RXNORM"
SAB_MESH = "MSH"
SAB_CPT = "CPT"


@dataclass(frozen=True)
class UMLSConcept:
    """Un concepto del Metatesauro UMLS."""

    cui: str  # Identificador Único de Concepto (p. ej., C0011849 = Diabetes Mellitus)
    name: str
    source: str  # Qué vocabulario (SNOMEDCT_US, ICD10CM, etc.)
    source_code: str  # Código en ese vocabulario


@lru_cache(maxsize=256)
def crosswalk(source: str, code: str, target: str) -> list[UMLSConcept]:
    """Mapea un código de un vocabulario a otro mediante el crosswalk de UMLS.

    Ejemplos:
        crosswalk("ICD10CM", "E11.9", "SNOMEDCT_US") → conceptos SNOMED de DM2
        crosswalk("RXNORM", "6809", "SNOMEDCT_US")   → conceptos SNOMED de metformina
        crosswalk("SNOMEDCT_US", "44054006", "ICD10CM") → códigos ICD-10 de DM2
    """
    if not UMLS_API_KEY:
        logger.debug("crosswalk UMLS omitido — sin clave de API")
        return []

    try:
        resp = httpx.get(
            f"{UMLS_BASE}/crosswalk/current/source/{source}/{code}",
            params={"targetSource": target, "apiKey": UMLS_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        results_list = data.get("result", [])
        if isinstance(results_list, dict):
            results_list = results_list.get("results", [])

        return [
            UMLSConcept(
                cui=r.get("ui", ""),
                name=r.get("name", ""),
                source=target,
                source_code=r.get("ui", ""),
            )
            for r in results_list
            if r.get("ui") != "NONE"
        ]
    except Exception as e:
        logger.debug("crosswalk UMLS falló %s/%s → %s: %s", source, code, target, e)
        return []


@lru_cache(maxsize=256)
def find_concept(term: str, source: str | None = None) -> list[UMLSConcept]:
    """Busca un concepto clínico en el Metatesauro UMLS.

    Si se especifica source, restringe la búsqueda a ese vocabulario.
    Devuelve hasta 5 conceptos coincidentes ordenados por relevancia.
    """
    if not UMLS_API_KEY:
        return []

    try:
        params: dict[str, str | int] = {
            "string": term,
            "apiKey": UMLS_API_KEY,
            "pageSize": 5,
        }
        if source:
            params["sab"] = source

        resp = httpx.get(
            f"{UMLS_BASE}/search/current",
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        results_list = data.get("result", {}).get("results", [])
        return [
            UMLSConcept(
                cui=r.get("ui", ""),
                name=r.get("name", ""),
                source=r.get("rootSource", source or ""),
                source_code=r.get("ui", ""),
            )
            for r in results_list
            if r.get("ui") != "NONE"
        ]
    except Exception as e:
        logger.debug("búsqueda UMLS falló para %s: %s", term, e)
        return []


def are_same_concept(
    code_a: tuple[str, str], code_b: tuple[str, str]
) -> bool:
    """Comprueba si dos conceptos codificados se refieren al mismo CUI de UMLS.

    Permite la correspondencia entre vocabularios:
        are_same_concept(("ICD10CM", "E11.9"), ("SNOMEDCT_US", "44054006")) → True
    """
    if not UMLS_API_KEY:
        return False

    # Obtener el CUI de code_a
    cui_a = _get_cui(code_a[0], code_a[1])
    if not cui_a:
        return False

    # Obtener el CUI de code_b
    cui_b = _get_cui(code_b[0], code_b[1])
    if not cui_b:
        return False

    return cui_a == cui_b


@lru_cache(maxsize=256)
def _get_cui(source: str, code: str) -> str | None:
    """Obtiene el CUI de UMLS para un código de vocabulario de origen."""
    if not UMLS_API_KEY:
        return None

    try:
        resp = httpx.get(
            f"{UMLS_BASE}/content/current/source/{source}/{code}",
            params={"apiKey": UMLS_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        result = data.get("result", {})
        # Extraer el CUI del URI del concepto
        concept_uri = result.get("concept", "")
        if concept_uri and "/" in concept_uri:
            return concept_uri.rsplit("/", 1)[-1]
        return None
    except Exception:
        return None


def enrich_with_crosswalk(
    source: str, code: str, display: str
) -> dict[str, str]:
    """Enriquece un concepto codificado con correspondencias entre vocabularios.

    Devuelve un dict de {vocabulario: código} para todas las correspondencias conocidas.
    """
    mappings: dict[str, str] = {source: code}

    targets = [SAB_SNOMEDCT, SAB_ICD10CM, SAB_RXNORM, SAB_LOINC]
    for target in targets:
        if target == source:
            continue
        results = crosswalk(source, code, target)
        if results:
            mappings[target] = results[0].source_code

    return mappings
