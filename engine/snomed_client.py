"""Cliente SNOMED CT — consulta de terminología clínica codificada.

Proporciona la comprobación de reactividad cruzada de alergias y la resolución de
conceptos clínicos mediante la API gratuita del navegador Snowstorm y la API REST UMLS.

Jerarquía de reactividad cruzada:
- Alergia a la penicilina (91936005) → los descendientes incluyen amoxicilina, ampicilina
- Betalactámicos (35631009) → clase más amplia que incluye las cefalosporinas
"""
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

UMLS_API_KEY = os.environ.get("UMLS_API_KEY", "")
UMLS_BASE = "https://uts-ws.nlm.nih.gov/rest"
SNOMED_BROWSER = "https://browser.ihtsdotools.org/snowstorm/snomed-ct"

_TIMEOUT = 10

# Jerarquías de clases farmacológicas SNOMED CT conocidas para reactividad cruzada
_DRUG_CLASS_HIERARCHY: dict[str, list[str]] = {
    "penicillin": [
        "amoxicillin", "ampicillin", "piperacillin", "nafcillin",
        "oxacillin", "dicloxacillin", "ticarcillin",
    ],
    "cephalosporin": [
        "cephalexin", "cefazolin", "ceftriaxone", "cefuroxime",
        "cefdinir", "cefepime", "ceftazidime",
    ],
    "sulfonamide": [
        "sulfamethoxazole", "sulfasalazine", "celecoxib",
        "trimethoprim-sulfamethoxazole",
    ],
    "fluoroquinolone": [
        "ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin",
    ],
    "opioid": [
        "morphine", "codeine", "hydrocodone", "oxycodone",
        "hydromorphone", "fentanyl", "tramadol",
    ],
    "nsaid": [
        "ibuprofen", "naproxen", "aspirin", "ketorolac",
        "indomethacin", "meloxicam", "diclofenac", "piroxicam",
    ],
    "ace inhibitor": [
        "lisinopril", "enalapril", "ramipril", "captopril",
        "benazepril", "fosinopril", "quinapril",
    ],
    "statin": [
        "atorvastatin", "simvastatin", "rosuvastatin", "pravastatin",
        "lovastatin", "fluvastatin", "pitavastatin",
    ],
}


@dataclass(frozen=True)
class SnomedConcept:
    """Un concepto SNOMED CT."""

    concept_id: str
    term: str
    semantic_tag: str  # "disorder", "substance", "finding", etc.


@lru_cache(maxsize=256)
def search_snomed(
    term: str, semantic_tag: str | None = None
) -> list[SnomedConcept]:
    """Busca un término clínico en SNOMED CT.

    Utiliza la API gratuita del navegador Snowstorm. Recurre a UMLS si hay clave de API configurada.
    """
    results = _search_snowstorm(term, semantic_tag)
    if not results and UMLS_API_KEY:
        results = _search_umls_snomed(term)
    return results


def _search_snowstorm(
    term: str, semantic_tag: str | None = None
) -> list[SnomedConcept]:
    """Busca mediante el navegador gratuito Snowstorm de SNOMED CT."""
    try:
        params: dict[str, str | int] = {"term": term, "limit": 5, "activeFilter": "true"}
        if semantic_tag:
            params["semanticTag"] = semantic_tag

        resp = httpx.get(
            f"{SNOMED_BROWSER}/MAIN/concepts",
            params=params,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        items = data.get("items", [])
        return [
            SnomedConcept(
                concept_id=str(item.get("conceptId", "")),
                term=item.get("fsn", {}).get("term", item.get("pt", {}).get("term", "")),
                semantic_tag=item.get("fsn", {}).get("term", "").split("(")[-1].rstrip(")") if "(" in item.get("fsn", {}).get("term", "") else "",
            )
            for item in items
        ]
    except Exception as e:
        logger.debug("búsqueda en Snowstorm falló para %s: %s", term, e)
        return []


def _search_umls_snomed(term: str) -> list[SnomedConcept]:
    """Busca SNOMED mediante la API REST UMLS (requiere UMLS_API_KEY)."""
    if not UMLS_API_KEY:
        return []
    try:
        resp = httpx.get(
            f"{UMLS_BASE}/search/current",
            params={
                "string": term,
                "sab": "SNOMEDCT_US",
                "apiKey": UMLS_API_KEY,
                "pageSize": 5,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results_list = data.get("result", {}).get("results", [])
        return [
            SnomedConcept(
                concept_id=r.get("ui", ""),
                term=r.get("name", ""),
                semantic_tag="",
            )
            for r in results_list
            if r.get("ui") != "NONE"
        ]
    except Exception as e:
        logger.debug("búsqueda SNOMED en UMLS falló para %s: %s", term, e)
        return []


def is_allergy_cross_reactive(allergy: str, medication: str) -> bool:
    """Comprueba si un medicamento presenta reactividad cruzada con una alergia conocida.

    Utiliza primero la jerarquía local de clases farmacológicas (rápida, determinista)
    y, si está disponible, recurre al recorrido de la jerarquía SNOMED CT.
    """
    allergy_lower = allergy.lower().strip()
    med_lower = medication.lower().strip()

    # Comprobar primero la jerarquía local (con expansión de alias)
    _ALIASES: dict[str, str] = {
        "sulfa": "sulfonamide",
        "penicillin v": "penicillin",
        "pen-vk": "penicillin",
    }
    expanded_allergy = _ALIASES.get(allergy_lower, allergy_lower)
    for class_name, members in _DRUG_CLASS_HIERARCHY.items():
        allergy_match = class_name in expanded_allergy or any(
            m in expanded_allergy for m in members
        )
        if allergy_match and any(m in med_lower for m in members):
            return True

    # Comprobar también entre clases: alergia a penicilina + cefalosporina (~2 % de reactividad cruzada)
    if "penicillin" in allergy_lower:
        cephalosporins = _DRUG_CLASS_HIERARCHY.get("cephalosporin", [])
        if any(c in med_lower for c in cephalosporins):
            return True  # Señalar para revisión clínica

    return False


def get_allergy_cross_reactions(allergy: str) -> list[str]:
    """Obtiene todos los medicamentos con reactividad cruzada respecto a una alergia dada."""
    allergy_lower = allergy.lower().strip()
    cross_reactive: list[str] = []

    for class_name, members in _DRUG_CLASS_HIERARCHY.items():
        if class_name in allergy_lower or any(m in allergy_lower for m in members):
            cross_reactive.extend(members)

    # Reactividad cruzada penicilina → cefalosporina
    if "penicillin" in allergy_lower:
        cross_reactive.extend(_DRUG_CLASS_HIERARCHY.get("cephalosporin", []))

    return list(set(cross_reactive))


def map_fhir_code_to_snomed(coding: dict) -> SnomedConcept | None:
    """Extrae el concepto SNOMED CT de una entrada de coding de un CodeableConcept FHIR."""
    system = coding.get("system", "")
    code = coding.get("code", "")
    display = coding.get("display", "")

    if "snomed" in system.lower() and code:
        return SnomedConcept(concept_id=code, term=display, semantic_tag="")

    # Si no es SNOMED, intentar localizarlo mediante búsqueda
    if display:
        results = search_snomed(display)
        return results[0] if results else None

    return None
