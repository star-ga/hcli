"""
Puntuación clínica — núcleos de puntuación deterministas para la memoria sanitaria.

Implementa los núcleos de puntuación de abstención, importancia y adversariales
en Python puro.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ClinicalConfidence:
    """Resultado del control de confianza — decide si debemos responder o abstenernos."""

    score: float  # 0.0-1.0
    should_abstain: bool
    reason: str

    @property
    def level(self) -> str:
        if self.score >= 0.8:
            return "high"
        if self.score >= 0.5:
            return "moderate"
        return "low"


def confidence_gate(
    bm25_scores: list[float],
    entity_overlaps: list[float],
    score_weight: float = 0.6,
    overlap_weight: float = 0.4,
    abstention_threshold: float = 0.3,
) -> ClinicalConfidence:
    """
    Control de confianza del núcleo abstention.mind.

    Decide si el sistema dispone de evidencia suficiente para responder a una consulta clínica.
    En el ámbito sanitario, abstenerse es mejor que adivinar.

    Args:
        bm25_scores: Puntuaciones de recuperación BM25 de los mejores resultados
        entity_overlaps: Ratios de solapamiento de entidades entre la consulta y los resultados
        score_weight: Peso de las puntuaciones de recuperación
        overlap_weight: Peso del solapamiento de entidades
        abstention_threshold: Por debajo de este valor, se abstiene de responder
    """
    if not bm25_scores:
        return ClinicalConfidence(
            score=0.0,
            should_abstain=True,
            reason="No se encontraron registros clínicos coincidentes",
        )

    n = len(bm25_scores)
    avg_score = sum(bm25_scores) / n
    avg_overlap = sum(entity_overlaps) / n
    confidence = avg_score * score_weight + avg_overlap * overlap_weight

    should_abstain = confidence < abstention_threshold
    if should_abstain:
        reason = (
            f"Confianza {confidence:.2f} por debajo del umbral clínico "
            f"{abstention_threshold}. Evidencia insuficiente para una respuesta segura."
        )
    else:
        reason = f"Confianza {confidence:.2f} — evidencia clínica suficiente."

    return ClinicalConfidence(
        score=confidence,
        should_abstain=should_abstain,
        reason=reason,
    )


def clinical_importance(
    access_count: int,
    days_since_access: float,
    connection_degree: int,
    is_acute: bool = False,
    decay_rate: float = -0.1,
    access_weight: float = 0.3,
    recency_weight: float = 0.5,
    connection_weight: float = 0.2,
) -> float:
    """
    Puntuación de importancia del núcleo importance.mind.

    Puntúa cuán importante es un bloque de memoria clínica para el contexto actual.
    Los procesos agudos reciben un incremento. Devuelve un multiplicador en [0.8, 1.5].
    """
    freq = math.log(access_count + 1)
    recency = math.exp(decay_rate * days_since_access)
    conn = math.log(connection_degree + 1)

    raw = access_weight * freq + recency_weight * recency + connection_weight * conn
    max_est = access_weight * 3.0 + recency_weight * 1.0 + connection_weight * 3.0
    normalized = raw / (max_est + 1e-6)
    score = 0.8 + normalized * 0.7

    if is_acute:
        score = min(score * 1.3, 1.5)

    return round(score, 4)


def medication_severity_score(
    interaction_type: str,
    severity: str | None = None,
) -> float:
    """
    Puntúa la gravedad de la interacción farmacológica para su priorización.

    Devuelve 0.0-1.0, donde un valor mayor = más grave.
    """
    base_scores = {
        "contraindicated": 1.0,
        "serious": 0.8,
        "moderate": 0.5,
        "minor": 0.2,
        "unknown": 0.4,
    }
    severity_boost = {
        "high": 0.15,
        "moderate": 0.05,
        "low": 0.0,
    }
    base = base_scores.get(interaction_type.lower(), 0.4)
    boost = severity_boost.get((severity or "").lower(), 0.0)
    return min(base + boost, 1.0)


def is_negation_query(query: str) -> bool:
    """
    Detección de negación del núcleo adversarial.mind.

    Esencial para consultas clínicas como "NO alérgico a la penicilina"
    frente a "alérgico a la penicilina".
    """
    negation_markers = [
        "not ", "no ", "never ", "without ", "absence of ",
        "denies ", "negative for ", "ruled out", "unlikely ",
        "non-", "un-", "n't ",
    ]
    lower = query.lower()
    return any(marker in lower for marker in negation_markers)


# Pares de interacción farmacológica conocidos (subconjunto de muestra — en producción se usaría la API de RxNorm)
_KNOWN_INTERACTIONS: list[tuple[str, str, str, str]] = [
    ("warfarin", "aspirin", "serious", "Mayor riesgo de hemorragia"),
    ("warfarin", "ibuprofen", "serious", "Mayor riesgo de hemorragia"),
    ("warfarin", "naproxen", "serious", "Mayor riesgo de hemorragia"),
    ("warfarin", "nsaid", "serious", "Mayor riesgo de hemorragia"),
    ("metformin", "contrast dye", "contraindicated", "Riesgo de acidosis láctica"),
    ("lisinopril", "potassium", "moderate", "Riesgo de hiperpotasemia"),
    ("lisinopril", "spironolactone", "moderate", "Riesgo de hiperpotasemia"),
    ("metoprolol", "verapamil", "serious", "Riesgo de bradicardia grave"),
    ("simvastatin", "amiodarone", "serious", "Mayor riesgo de rabdomiólisis"),
    ("fluoxetine", "tramadol", "serious", "Riesgo de síndrome serotoninérgico"),
    ("ciprofloxacin", "tizanidine", "contraindicated", "Hipotensión peligrosa"),
    ("methotrexate", "trimethoprim", "serious", "Mayor toxicidad por metotrexato"),
]

# Reacciones cruzadas por alergia conocidas
_ALLERGY_CROSS_REACTIONS: list[tuple[str, list[str], str]] = [
    ("penicillin", ["amoxicillin", "ampicillin", "piperacillin"], "Reactividad cruzada de betalactámicos"),
    ("sulfa", ["sulfamethoxazole", "sulfasalazine", "celecoxib"], "Reactividad cruzada de sulfonamidas"),
    ("codeine", ["morphine", "hydrocodone", "oxycodone"], "Sensibilidad cruzada a opioides"),
    ("nsaid", ["ibuprofen", "naproxen", "aspirin", "ketorolac"], "Reacción de clase de los AINE"),
]


@dataclass(frozen=True)
class DrugInteraction:
    drug_a: str
    drug_b: str
    severity: str
    description: str
    score: float


def check_drug_interactions(
    medications: list[str], use_llm_fallback: bool = True
) -> list[DrugInteraction]:
    """
    Comprueba una lista de medicaciones en busca de interacciones.

    Canalización de detección en cuatro capas:
    1. Tabla determinista (12 pares conocidos) — rápida, fiable, auditable
    2. API de OpenEvidence — autoridad clínica, diseñada específicamente para medicina
    3. API de interacciones de RxNorm — gratuita, sin autenticación, referencia clínica consolidada
    4. LLM Gemini — respaldo de propósito general para los pares restantes sin cubrir

    Cada capa solo comprueba los pares que no han encontrado ya las capas anteriores.
    """
    meds_lower = [m.lower().strip() for m in medications]
    interactions = []

    # Capa 1: Tabla determinista (microsegundos)
    covered_pairs: set[tuple[str, str]] = set()
    for drug_a, drug_b, severity, description in _KNOWN_INTERACTIONS:
        a_match = any(drug_a in m for m in meds_lower)
        b_match = any(drug_b in m for m in meds_lower)
        if a_match and b_match:
            interactions.append(
                DrugInteraction(
                    drug_a=drug_a,
                    drug_b=drug_b,
                    severity=severity,
                    description=description,
                    score=medication_severity_score(severity),
                )
            )
            covered_pairs.add((drug_a, drug_b))

    # Capa 2: API de OpenEvidence (autoridad clínica, diseñada específicamente para medicina)
    if use_llm_fallback and len(meds_lower) >= 2:
        oe_interactions = _openevidence_check_interactions(medications, covered_pairs)
        interactions.extend(oe_interactions)
        for i in oe_interactions:
            covered_pairs.add((i.drug_a, i.drug_b))

    # Capa 3: API de RxNorm — normalización adecuada de fármacos + BD de interacciones
    if use_llm_fallback and len(meds_lower) >= 2:
        rxnorm_interactions = _rxnorm_check_interactions(medications, covered_pairs)
        interactions.extend(rxnorm_interactions)
        for i in rxnorm_interactions:
            covered_pairs.add((i.drug_a, i.drug_b))

    # Capa 4: Respaldo Gemini (LLM de propósito general para los pares restantes sin cubrir)
    if use_llm_fallback and len(meds_lower) >= 2:
        llm_interactions = _llm_check_interactions(medications, covered_pairs)
        interactions.extend(llm_interactions)

    return sorted(interactions, key=lambda i: i.score, reverse=True)


def _openevidence_check_interactions(
    medications: list[str],
    already_found: set[tuple[str, str]],
) -> list[DrugInteraction]:
    """
    Capa 2: API de OpenEvidence — comprobación de interacciones con autoridad clínica.

    OpenEvidence está diseñada específicamente para medicina. A diferencia de un LLM
    general, devuelve respuestas fundamentadas en evidencia con citas a literatura
    revisada por pares.

    API: POST https://api.openevidence.com/analysis
    Autenticación: basada en token (OPENEVIDENCE_API_KEY)
    """
    import json
    import logging
    import os

    logger = logging.getLogger(__name__)

    api_key = os.environ.get("OPENEVIDENCE_API_KEY")
    if not api_key:
        return []

    med_names = [m.split()[0] if " " in m else m for m in medications]
    query = (
        f"¿Existen interacciones farmacológicas clínicamente significativas entre alguna de "
        f"estas medicaciones: {', '.join(med_names)}? "
        f"Para cada interacción encontrada, indique los dos fármacos, la gravedad "
        f"(grave o contraindicada) y una descripción clínica de una frase."
    )

    try:
        import httpx

        resp = httpx.post(
            "https://api.openevidence.com/analysis",
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"text": query, "model": "oe-v2"},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.warning("La API de OpenEvidence devolvió %d", resp.status_code)
            return []

        data = resp.json()
        # OpenEvidence devuelve un análisis narrativo — se analiza en busca de pares de fármacos
        analysis_text = data.get("text", "") or data.get("analysis", "") or str(data)
        if not analysis_text:
            return []

        results = _parse_interaction_narrative(
            analysis_text, med_names, already_found, source="OpenEvidence"
        )
        if results:
            logger.info(
                "OpenEvidence detectó %d interacciones adicionales", len(results)
            )
        return results

    except Exception as e:
        logger.warning("Falló la llamada a la API de OpenEvidence: %s", e)
        return []


def _parse_interaction_narrative(
    text: str,
    med_names: list[str],
    already_found: set[tuple[str, str]],
    source: str = "OpenEvidence",
) -> list[DrugInteraction]:
    """Analiza un texto narrativo en busca de menciones de interacciones farmacológicas."""
    text_lower = text.lower()
    results = []
    meds_lower = [m.lower() for m in med_names]

    # Comprueba todos los pares de medicación frente a la narrativa
    for i, med_a in enumerate(meds_lower):
        for med_b in meds_lower[i + 1 :]:
            if (med_a, med_b) in already_found or (med_b, med_a) in already_found:
                continue
            # Ambas medicaciones mencionadas en el texto de análisis = interacción potencial
            if med_a in text_lower and med_b in text_lower:
                # Comprueba los indicadores de gravedad
                severity = "moderate"
                if any(
                    w in text_lower
                    for w in [
                        "contraindicated",
                        "avoid",
                        "do not combine",
                        "prohibited",
                    ]
                ):
                    severity = "contraindicated"
                elif any(
                    w in text_lower
                    for w in [
                        "serious",
                        "significant",
                        "major",
                        "dangerous",
                        "bleeding risk",
                        "serotonin syndrome",
                        "qt prolongation",
                    ]
                ):
                    severity = "serious"

                if severity in ("serious", "contraindicated"):
                    # Extrae un fragmento de descripción en torno a las menciones de los fármacos
                    desc = f"Interacción detectada por {source} entre {med_a} y {med_b}"
                    results.append(
                        DrugInteraction(
                            drug_a=med_a,
                            drug_b=med_b,
                            severity=severity,
                            description=desc,
                            score=medication_severity_score(severity),
                        )
                    )
    return results


def _rxnorm_check_interactions(
    medications: list[str],
    already_found: set[tuple[str, str]],
) -> list[DrugInteraction]:
    """Capa 3: API de RxNorm — normalización adecuada de fármacos + BD de interacciones.

    Usa el módulo rxnorm_client para:
    1. Resolución del nombre canónico del fármaco (marca → genérico → principio activo)
    2. Búsqueda de interacciones basada en RxCUI (API de interacciones de RxNorm)
    Fuente de datos ampliamente utilizada por los sistemas de historia clínica electrónica.
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        from engine.rxnorm_client import normalize_medication_list, get_interactions_for_list
    except ImportError:
        return []

    resolved = normalize_medication_list(medications)
    rxcuis = [rc.rxcui for rc in resolved.values() if rc is not None]

    if len(rxcuis) < 2:
        return []

    rxnorm_interactions = get_interactions_for_list(rxcuis)
    results = []

    for ri in rxnorm_interactions:
        pair = (ri.drug_a, ri.drug_b)
        if pair in already_found or tuple(reversed(pair)) in already_found:
            continue

        if ri.severity in ("serious", "contraindicated"):
            results.append(
                DrugInteraction(
                    drug_a=ri.drug_a,
                    drug_b=ri.drug_b,
                    severity=ri.severity,
                    description=f"RxNorm/{ri.source}: {ri.description[:200]}",
                    score=medication_severity_score(ri.severity),
                )
            )
            already_found.add(pair)

    if results:
        logger.info("La API de RxNorm detectó %d interacciones adicionales", len(results))
    return results


def _call_openai_json(prompt: str, api_key: str) -> str | None:
    """Llama a la API de OpenAI para obtener una respuesta JSON estructurada de interacciones farmacológicas."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        import httpx

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-5.4",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Eres un farmacéutico clínico con experiencia en interacciones "
                            "entre fármacos. Devuelves ÚNICAMENTE arrays JSON válidos."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 512,
            },
            timeout=5,
        )
        if resp.status_code != 200:
            logger.info("OpenAI devolvió %d", resp.status_code)
            return None
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.info("Falló la llamada a OpenAI: %s", e)
        return None


def _call_google_json(prompt: str, api_key: str, model_id: str) -> str | None:
    """Llama a la API de Google GenAI para obtener una respuesta JSON estructurada de interacciones farmacológicas."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        import httpx

        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512},
            },
            timeout=5,
        )
        if resp.status_code != 200:
            logger.info("%s devolvió %d", model_id, resp.status_code)
            return None
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    except Exception as e:
        logger.info("%s falló: %s", model_id, e)
    return None


def _llm_check_interactions(
    medications: list[str],
    already_found: set[tuple[str, str]],
) -> list[DrugInteraction]:
    """
    Capa 4: Cascada de LLM médicos para interacciones farmacológicas.

    Prueba los modelos en orden de solidez clínica, usando las claves de API
    que estén disponibles. Cada modelo usa el mismo prompt estructurado.

    Cascada: OpenAI GPT-5.4 → MedGemma 27B → Gemini Flash
    """
    import json
    import logging
    import os

    logger = logging.getLogger(__name__)

    openai_key = os.environ.get("OPENAI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    if not openai_key and not google_key:
        return []

    med_names = [m.split()[0] if " " in m else m for m in medications]

    prompt = f"""Eres un farmacéutico clínico. Dadas estas medicaciones: {', '.join(med_names)}

Comprueba las interacciones entre fármacos. Informa ÚNICAMENTE de interacciones clínicamente
significativas (graves o contraindicadas). NO informes de interacciones menores ni teóricas.

Responde ÚNICAMENTE con un array JSON. Cada elemento debe tener:
- "drug_a": nombre del primer fármaco (en minúsculas)
- "drug_b": nombre del segundo fármaco (en minúsculas)
- "severity": "serious" o "contraindicated"
- "description": descripción clínica de una frase

Si NO existen interacciones significativas, responde con: []

Array JSON:"""

    # Construye la cascada de modelos según las claves de API disponibles
    attempts: list[tuple[str, callable]] = []
    if openai_key:
        attempts.append(("OpenAI-GPT-5.4", lambda: _call_openai_json(prompt, openai_key)))
    if google_key:
        attempts.append(("MedGemma", lambda: _call_google_json(prompt, google_key, "medgemma-27b-text-v1")))
        attempts.append(("Gemini", lambda: _call_google_json(prompt, google_key, "gemini-3-flash")))

    for model_label, call_fn in attempts:
        text = call_fn()
        if not text:
            continue

        # Extrae el JSON (gestiona los bloques de código markdown)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        if not text or text == "[]":
            return []

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            logger.info("%s devolvió un JSON no válido, probando con el siguiente", model_label)
            continue
        if not isinstance(parsed, list):
            continue

        results = []
        for item in parsed:
            a = item.get("drug_a", "").lower()
            b = item.get("drug_b", "").lower()
            if (a, b) in already_found or (b, a) in already_found:
                continue
            sev = item.get("severity", "moderate")
            if sev not in ("serious", "contraindicated"):
                continue
            results.append(
                DrugInteraction(
                    drug_a=a,
                    drug_b=b,
                    severity=sev,
                    description=f"{model_label}: {item.get('description', 'Interacción detectada por LLM')}",
                    score=medication_severity_score(sev),
                )
            )
        if results:
            logger.info("%s detectó %d interacciones farmacológicas adicionales", model_label, len(results))
        return results

    return []


@dataclass(frozen=True)
class AllergyConflict:
    allergen: str
    medication: str
    cross_reaction_group: str
    description: str


def check_allergy_conflicts(
    allergies: list[str], medications: list[str]
) -> list[AllergyConflict]:
    """Compara las alergias del paciente frente a las medicaciones prescritas.

    Detección en dos capas:
    1. Tabla local de reacciones cruzadas (rápida, determinista)
    2. Jerarquía de clases de fármacos SNOMED CT (cobertura más amplia)
    """
    allergies_lower = [a.lower().strip() for a in allergies]
    meds_lower = [m.lower().strip() for m in medications]
    conflicts = []
    found_pairs: set[tuple[str, str]] = set()

    # Capa 1: Tabla determinista
    for allergen, cross_drugs, description in _ALLERGY_CROSS_REACTIONS:
        allergen_match = any(allergen in a for a in allergies_lower)
        if not allergen_match:
            continue
        for drug in cross_drugs:
            drug_match = any(drug in m for m in meds_lower)
            if drug_match:
                conflicts.append(
                    AllergyConflict(
                        allergen=allergen,
                        medication=drug,
                        cross_reaction_group=allergen,
                        description=description,
                    )
                )
                found_pairs.add((allergen, drug))

    # Capa 2: Jerarquía de clases de fármacos SNOMED CT (cobertura más amplia)
    try:
        from engine.snomed_client import is_allergy_cross_reactive
        for allergy in allergies_lower:
            for med in meds_lower:
                if (allergy, med) in found_pairs:
                    continue
                if is_allergy_cross_reactive(allergy, med):
                    conflicts.append(
                        AllergyConflict(
                            allergen=allergy,
                            medication=med,
                            cross_reaction_group="SNOMED-hierarchy",
                            description="Reactividad cruzada detectada mediante la jerarquía de clases de fármacos",
                        )
                    )
                    found_pairs.add((allergy, med))
    except ImportError:
        pass

    return conflicts


# ── Contraindicaciones de medicación basadas en laboratorio ───────────────────

# Asigna (patrón_nombre_laboratorio, patrón_medicación) -> lógica de umbral
# Cada entrada: (lab_keywords, med_keywords, threshold, direction, severity, description, recommendation)
_LAB_MED_CONTRAINDICATIONS: list[tuple[list[str], list[str], float, str, str, str, str]] = [
    (
        ["egfr", "gfr", "glomerular filtration"],
        ["metformin"],
        30.0, "below", "critical",
        "La metformina está contraindicada cuando el FGe cae por debajo de 30 mL/min por riesgo de acidosis láctica",
        "SUSPENDER la metformina de inmediato. Considerar insulina o un inhibidor de la DPP-4 (con dosis ajustada a la función renal).",
    ),
    (
        ["egfr", "gfr", "glomerular filtration"],
        ["metformin"],
        45.0, "below", "high",
        "Se recomienda reducir la dosis de metformina cuando el FGe está entre 30 y 45 mL/min",
        "Reducir la metformina a un máximo de 1000 mg/día. Monitorizar la función renal cada 3 meses.",
    ),
    (
        ["inr"],
        ["warfarin"],
        3.5, "above", "high",
        "Un INR por encima del rango terapéutico (2,0-3,0) indica anticoagulación excesiva y riesgo de hemorragia",
        "Suspender la dosis de warfarina. Comprobar nuevas medicaciones interactuantes (AINE, antibióticos). Repetir el INR en 2-3 días.",
    ),
    (
        ["potassium", "k+"],
        ["lisinopril", "losartan", "spironolactone"],
        5.5, "above", "critical",
        "Riesgo de hiperpotasemia con IECA/ARA-II/antagonistas de la aldosterona cuando el potasio supera 5,5 mEq/L",
        "Suspender las medicaciones ahorradoras de potasio. Obtener un ECG urgente. Considerar gluconato cálcico si K+ > 6,0.",
    ),
    (
        ["hba1c", "hemoglobin a1c", "a1c"],
        ["metformin"],
        9.0, "above", "moderate",
        "Una HbA1c por encima del 9 % sugiere un control glucémico inadecuado con la pauta actual de metformina",
        "Considerar añadir un agente de segunda línea (agonista del GLP-1 o inhibidor del SGLT2). Reforzar las modificaciones del estilo de vida.",
    ),
]


@dataclass(frozen=True)
class LabMedContraindication:
    lab_name: str
    lab_value: float
    lab_unit: str
    medication: str
    threshold: float
    direction: str  # "above" o "below"
    severity: str
    description: str
    recommendation: str


def check_lab_medication_contraindications(
    observations: list[dict], medications: list[str]
) -> list[LabMedContraindication]:
    """
    Compara los resultados de laboratorio frente a las medicaciones en busca de contraindicaciones.

    Detecta combinaciones clínicamente peligrosas como FG en descenso + metformina,
    INR elevado + warfarina, o hiperpotasemia + IECA.

    Args:
        observations: Lista de dicts de observación con claves: observation_name, value, unit
        medications: Lista de nombres de medicaciones activas
    """
    meds_lower = [m.lower().strip() for m in medications]
    contraindications = []

    for lab_keywords, med_keywords, threshold, direction, severity, desc, rec in _LAB_MED_CONTRAINDICATIONS:
        # Comprueba si coincide alguna medicación
        med_match = None
        for med_kw in med_keywords:
            for m in meds_lower:
                if med_kw in m:
                    med_match = m
                    break
            if med_match:
                break
        if not med_match:
            continue

        # Encuentra las observaciones de laboratorio coincidentes
        for obs in observations:
            obs_name = (obs.get("observation_name") or obs.get("name") or "").lower()
            if not any(kw in obs_name for kw in lab_keywords):
                continue
            try:
                val = float(obs.get("value", 0))
            except (ValueError, TypeError):
                continue
            unit = obs.get("unit") or obs.get("lab_unit") or ""

            triggered = (
                (direction == "below" and val < threshold) or
                (direction == "above" and val > threshold)
            )
            if triggered:
                contraindications.append(
                    LabMedContraindication(
                        lab_name=obs.get("observation_name") or obs.get("name") or "Desconocido",
                        lab_value=val,
                        lab_unit=unit,
                        medication=med_match,
                        threshold=threshold,
                        direction=direction,
                        severity=severity,
                        description=desc,
                        recommendation=rec,
                    )
                )

    # Elimina duplicados: conserva solo la mayor gravedad por par (lab_name, medication)
    seen = {}
    severity_rank = {"critical": 4, "high": 3, "moderate": 2, "low": 1}
    for c in contraindications:
        key = (c.lab_name, c.medication)
        existing = seen.get(key)
        if not existing or severity_rank.get(c.severity, 0) > severity_rank.get(existing.severity, 0):
            seen[key] = c

    return sorted(seen.values(), key=lambda c: severity_rank.get(c.severity, 0), reverse=True)


# ── Análisis de tendencias de laboratorio ─────────────────────────────────────

@dataclass(frozen=True)
class LabTrend:
    lab_name: str
    values: list[float]
    dates: list[str]
    direction: str  # "declining", "rising", "stable"
    rate_of_change: float  # cambio medio por medición
    severity: str
    description: str
    recommendation: str


def detect_lab_trends(observations: list[dict]) -> list[LabTrend]:
    """
    Detecta tendencias clínicamente significativas en valores de laboratorio secuenciales.

    Agrupa las observaciones por nombre de laboratorio, las ordena por fecha e identifica
    patrones descendentes o ascendentes que requieren atención clínica.
    """
    # Agrupa las observaciones por nombre
    by_name: dict[str, list[tuple[str, float]]] = {}
    for obs in observations:
        name = (obs.get("observation_name") or obs.get("name") or "").strip()
        date = obs.get("effective_date") or obs.get("date") or ""
        try:
            val = float(obs.get("value", 0))
        except (ValueError, TypeError):
            continue
        if name and date:
            by_name.setdefault(name, []).append((date, val))

    trends = []
    for name, points in by_name.items():
        if len(points) < 2:
            continue
        # Ordena por fecha
        points.sort(key=lambda x: x[0])
        values = [p[1] for p in points]
        dates = [p[0] for p in points]

        # Calcula la tendencia
        changes = [values[i+1] - values[i] for i in range(len(values)-1)]
        avg_change = sum(changes) / len(changes)
        total_change = values[-1] - values[0]

        name_lower = name.lower()

        # Tendencia descendente del FG
        if any(kw in name_lower for kw in ["egfr", "gfr", "glomerular"]):
            if total_change < -5:  # Descenso de 5 mL/min o más
                severity = "critical" if values[-1] < 30 else "high" if values[-1] < 45 else "moderate"
                trends.append(LabTrend(
                    lab_name=name,
                    values=values,
                    dates=dates,
                    direction="declining",
                    rate_of_change=round(avg_change, 2),
                    severity=severity,
                    description=(
                        f"FGe en descenso: {values[0]:.0f} → {values[-1]:.0f} mL/min/1,73 m² "
                        f"(Δ {total_change:+.0f} a lo largo de {len(values)} mediciones). "
                        f"{'Se aproxima al umbral de contraindicación de medicaciones nefrotóxicas.' if values[-1] < 45 else 'Monitorizar estrechamente.'}"
                    ),
                    recommendation=(
                        "Revisar el ajuste de dosis de todas las medicaciones de eliminación renal. "
                        "Derivar a nefrología si no participa ya en el caso. "
                        "Repetir el FGe en 4-6 semanas."
                    ),
                ))

        # INR con tendencia al alza
        elif any(kw in name_lower for kw in ["inr"]):
            if total_change > 0.5 and values[-1] > 3.0:
                trends.append(LabTrend(
                    lab_name=name,
                    values=values,
                    dates=dates,
                    direction="rising",
                    rate_of_change=round(avg_change, 2),
                    severity="high",
                    description=(
                        f"INR en ascenso por encima del rango terapéutico: {values[0]:.1f} → {values[-1]:.1f}. "
                        "Comprobar nuevas medicaciones interactuantes o cambios en la dieta."
                    ),
                    recommendation="Suspender la warfarina. Investigar la causa. Repetir el INR en 2-3 días.",
                ))

    return trends


# ── Detección de desacuerdos entre profesionales ──────────────────────────────

@dataclass(frozen=True)
class ProviderDisagreement:
    topic: str
    provider_a: str
    provider_a_position: str
    provider_b: str
    provider_b_position: str
    severity: str
    description: str
    recommendation: str


def detect_provider_disagreements(blocks: list[dict]) -> list[ProviderDisagreement]:
    """
    Detecta recomendaciones clínicas contradictorias de distintos profesionales.

    Compara las notas y los objetivos entre las observaciones/bloques de distintos
    profesionales para hallar desacuerdos en los objetivos del tratamiento.
    """
    disagreements = []

    # Busca conflictos de objetivo de TA en las notas de las observaciones
    bp_targets: list[dict] = []
    for block in blocks:
        content = (block.get("content") or "").lower()
        source = block.get("source") or block.get("metadata", {}).get("performer") or ""
        title = (block.get("title") or "").lower()
        notes = block.get("metadata", {}).get("notes") or ""

        # Comprueba las menciones de objetivo de TA
        if "blood pressure" in title or "bp" in title:
            import re
            # Coincide con patrones como "<130/80", "target: 130/80", "<140/90"
            target_match = re.search(r'target[:\s]*<?(\d{2,3})/(\d{2,3})', content + " " + notes.lower())
            if target_match:
                systolic = int(target_match.group(1))
                diastolic = int(target_match.group(2))
                bp_targets.append({
                    "systolic": systolic,
                    "diastolic": diastolic,
                    "source": source,
                    "content": content,
                })

    # Compara los objetivos de TA de distintos profesionales
    for i in range(len(bp_targets)):
        for j in range(i + 1, len(bp_targets)):
            a, b = bp_targets[i], bp_targets[j]
            if a["source"] == b["source"]:
                continue
            systolic_diff = abs(a["systolic"] - b["systolic"])
            if systolic_diff >= 10:
                disagreements.append(ProviderDisagreement(
                    topic="Objetivo de tensión arterial",
                    provider_a=a["source"],
                    provider_a_position=f"Objetivo <{a['systolic']}/{a['diastolic']} mmHg",
                    provider_b=b["source"],
                    provider_b_position=f"Objetivo <{b['systolic']}/{b['diastolic']} mmHg",
                    severity="high",
                    description=(
                        f"Desacuerdo entre profesionales sobre el objetivo de TA: {a['source']} recomienda "
                        f"<{a['systolic']}/{a['diastolic']}, pero {b['source']} recomienda "
                        f"<{b['systolic']}/{b['diastolic']}. Diferencia sistólica de {systolic_diff} mmHg."
                    ),
                    recommendation=(
                        "Programar una reunión de coordinación asistencial entre los profesionales. "
                        "Considerar las comorbilidades del paciente (ERC frente a riesgo cardiovascular) "
                        "para establecer un objetivo de TA unificado."
                    ),
                ))

    return disagreements
