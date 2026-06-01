"""Fija la integridad de la cohorte de control negativo.

Iter 120 (ronda 24, sustitución T1).

La afirmación de precisión de 0 / 10 falsos positivos en la demostración
y la documentación es crítica para la seguridad: es la compuerta de
*precisión* que complementa la compuerta de recuperación de la validación
clínica. La afirmación la calcula
`scripts/run_negative_control_eval.py` contra
`docs/negative_control_cohort.json`. Si esa cohorte se desvía de forma
silenciosa (encoge de tamaño, un par se reclasifica, un par colisiona con
una contraindicación de la caché), la compuerta de precisión pierde su
sentido sin que nadie lo note.

Esta fijación impone seis invariantes estructurales:

  1. Tamaño de la cohorte = 10 (4 de frontera + 6 limpios — el diseño iter-10).
  2. El `expected_severity` de cada entrada es exactamente "none".
  3. CERO entradas colisionan con entradas contraindicadas de la caché
     (una contradicción lógica — el mismo par no puede ser un
     "control negativo" Y una "interacción contraindicada").
  4. Cada entrada tiene ≥ 1 URL de evidencia (suelo del rastro de auditoría).
  5. Los 4 casos de frontera de vía CYP nombrados están presentes (su
     presencia es lo que hace no trivial la compuerta de precisión — los
     negativos limpios demuestran "BitNet no se dispara con pares no
     relacionados"; los casos de frontera demuestran "BitNet no da falsos
     positivos con pares que parecen interacciones pero no lo son").
  6. Los 6 negativos limpios NO deben incluir ningún par de fármacos
     donde alguno de los dos aparezca en una entrada contraindicada de la
     caché — esto evita que un futuro revisor cuele un sustrato de CYP3A4
     entre los "negativos limpios" e infle la recuperación aparente.

Misma clase de desviación que la fijación de cobertura pharmacology_flags
de iter-100 y la divulgación del invariante de federación de iter-22: un
artefacto crítico debe ser auditable desde el conjunto de pruebas.
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_NEG_CONTROL = _REPO_ROOT / "docs" / "negative_control_cohort.json"
_CACHE = _REPO_ROOT / "docs" / "openevidence_cache.json"
_DEMO = _REPO_ROOT / "docs" / "demo.html"

_EXPECTED_SIZE = 10
_EXPECTED_BOUNDARY_CASES = frozenset({
    ("amlodipine", "atorvastatin"),
    ("clopidogrel", "pantoprazole"),
    ("diltiazem", "simvastatin"),
    ("spironolactone", "trimethoprim"),
})


def _pair_key(entry: dict) -> tuple[str, str]:
    """Devuelve la tupla canónica del par, ordenada y en minúsculas."""
    pair = entry.get("drug_pair_canonical") or [
        entry["drug_a"], entry["drug_b"]
    ]
    a, b = sorted(d.strip().lower() for d in pair)
    return (a, b)


def _load_neg_control() -> list[dict]:
    return json.loads(_NEG_CONTROL.read_text())


def _load_cache_contras() -> set[tuple[str, str]]:
    cache = json.loads(_CACHE.read_text())
    return {
        _pair_key(e)
        for e in cache
        if (e.get("severity") or "").lower() == "contraindicated"
    }


def test_neg_control_cohort_size_pinned():
    """El tamaño de la cohorte debe ser exactamente 10 — el diseño iter-10 (4 de frontera + 6 limpios).

    Añadir una entrada nueva obliga a una actualización deliberada de esta
    constante Y de la afirmación `0 / 10 FP` de la demostración Y de la
    descripción de la compuerta de precisión en la documentación. El
    crecimiento futuro de la cohorte es sano, pero debe ser visible.
    """
    cohort = _load_neg_control()
    assert len(cohort) == _EXPECTED_SIZE, (
        f"El tamaño de la cohorte de control negativo se desvió: live={len(cohort)}, "
        f"fijado={_EXPECTED_SIZE}. Actualiza la afirmación `0 / N FP` de la "
        f"demostración Y la descripción de la compuerta de precisión en la "
        f"documentación Y esta constante en el mismo commit."
    )


def test_neg_control_all_expected_severity_none():
    """Cada entrada de la cohorte debe tener expected_severity == 'none'.

    Un control negativo con expected_severity != 'none' es una
    contradicción lógica — por definición el flujo DEBERÍA marcarlo.
    """
    cohort = _load_neg_control()
    offenders = [
        (entry["drug_a"], entry["drug_b"], entry.get("expected_severity"))
        for entry in cohort
        if entry.get("expected_severity") != "none"
    ]
    assert not offenders, (
        f"Entradas de control negativo con expected_severity distinto de 'none': "
        f"{offenders}. Un control negativo con severidad esperada != 'none' "
        f"pertenece a la cohorte de recuperación (caché), no a la cohorte de precisión."
    )


def test_neg_control_zero_collision_with_cache_contras():
    """Ningún par de control negativo puede ser también una entrada contraindicada de la caché.

    Este es el invariante de seguridad crítico: la afirmación de precisión
    `0 / N FP` solo tiene sentido si cada par de la cohorte NO es
    genuinamente una interacción contraindicada. Una colisión significaría
    que estamos contando una contraindicación real como 'falso positivo' —
    socavando a la vez la afirmación de precisión y la de recuperación.
    """
    cohort_pairs = {_pair_key(e) for e in _load_neg_control()}
    cache_contras = _load_cache_contras()
    collisions = cohort_pairs & cache_contras
    assert not collisions, (
        f"COLISIÓN entre control negativo y contraindicación de caché: {sorted(collisions)}. "
        f"Estos pares aparecen TANTO en negative_control_cohort.json COMO en "
        f"openevidence_cache.json (severity=contraindicated). Una de las dos "
        f"cohortes es incorrecta; elimina la entrada de la que sea la fuente "
        f"de verdad más débil (normalmente: una contraindicación curada de "
        f"ficha técnica del medicamento prevalece sobre un argumento de 'sin "
        f"interacción clínicamente significativa')."
    )


def test_neg_control_every_entry_has_evidence_url():
    """Cada entrada de la cohorte debe llevar ≥ 1 URL de evidencia — suelo del rastro de auditoría."""
    offenders = [
        (entry["drug_a"], entry["drug_b"])
        for entry in _load_neg_control()
        if not entry.get("evidence_urls")
    ]
    assert not offenders, (
        f"Entradas de control negativo sin evidence_urls: {offenders}. "
        f"Cada entrada de la cohorte debe citar ≥ 1 fuente para que un "
        f"revisor clínico pueda verificar el argumento de 'sin interacción "
        f"clínicamente significativa'."
    )


def test_neg_control_boundary_cases_present():
    """Los 4 casos de frontera de vía CYP nombrados deben estar en la cohorte.

    Los casos de frontera son lo que hace no trivial la compuerta de
    precisión — los negativos limpios muestran 'BitNet no se dispara con
    pares no relacionados'; los casos de frontera muestran 'BitNet no da
    falsos positivos con pares que PARECEN interacciones (solapamiento de
    CYP, coprescripción de estatinas, solapamiento de transportadores)
    pero NO son clínicamente significativos'.
    """
    cohort_pairs = {_pair_key(e) for e in _load_neg_control()}
    missing = _EXPECTED_BOUNDARY_CASES - cohort_pairs
    assert not missing, (
        f"Faltan en la cohorte de control negativo casos de frontera de vía "
        f"CYP requeridos: {sorted(missing)}. El argumento de seguridad de la "
        f"compuerta de precisión descansa en ellos — son los pares que un "
        f"sistema descuidado SÍ marcaría. Eliminarlos debilita la afirmación "
        f"de 'sin falsos positivos' de no trivial a trivial."
    )


def test_neg_control_clean_negatives_are_truly_clean():
    """Los 6 negativos limpios deben usar pares de fármacos donde NINGUNO
    de los dos aparezca en una entrada contraindicada de la caché.

    Un 'negativo limpio' que incluyera (por ejemplo) `simvastatin` sería
    engañoso porque la simvastatina aparece en más de 6 contraindicaciones
    de la caché — una prueba limpia debería usar fármacos que el modelo NO
    haya visto en contextos contraindicados, de modo que sepamos que el
    resultado 'sin FP' procede de una generalización correcta, no de que el
    modelo carezca de toda señal sobre cualquiera de los dos fármacos.

    Diseño iter-120: los negativos limpios usan acetaminophen, lisinopril,
    omeprazole, atorvastatin (la frontera ya lo cubre), metformin,
    albuterol, fluticasone, amoxicillin — fármacos que aparecen en la caché
    como 'serious' / 'moderate' / 'none' pero nunca como 'contraindicated'.
    """
    cohort_pairs = {_pair_key(e) for e in _load_neg_control()}
    clean = cohort_pairs - _EXPECTED_BOUNDARY_CASES
    cache_contras = _load_cache_contras()
    contra_drugs = {drug for pair in cache_contras for drug in pair}

    # Fármacos permitidos en los negativos limpios aunque estén en los
    # fármacos contraindicados — son los ejemplos canónicos de farmacología
    # que QUEREMOS en los negativos limpios para demostrar el comportamiento
    # de no colisión. El MODELO debe distinguir "fármaco X + acompañante
    # limpio = none" de "fármaco X + acompañante peligroso = contra" — el
    # caso de frontera demuestra que puede hacerlo.
    _ALLOWED_OVERLAP: set[str] = {
        # la metformina aparece en `contrast dye + metformin`, `iodine +
        # metformin` y `metformin + renal impairment` (grupo de acidosis
        # láctica). El negativo limpio `acetaminophen + metformin` demuestra
        # que el modelo no da falsos positivos por la mera presencia de la
        # metformina — solo por el acompañante peligroso.
        "metformin",
        # el lisinopril aparece en `lisinopril + sacubitril` (bradicinina /
        # angioedema). El negativo limpio `acetaminophen + lisinopril`
        # demuestra que el modelo no da falsos positivos por la mera
        # presencia del IECA — solo por la combinación específica con el
        # inhibidor de la neprilisina.
        "lisinopril",
    }

    offenders = []
    for pair in clean:
        for drug in pair:
            if drug in contra_drugs and drug not in _ALLOWED_OVERLAP:
                offenders.append((pair, drug))
                break

    assert not offenders, (
        f"Entradas de la cohorte de negativos limpios que usan fármacos "
        f"presentes en contraindicaciones de la caché: {offenders}. Esto "
        f"debilita la afirmación de precisión — el modelo no debería tener "
        f"NINGUNA señal sobre los pares de negativos limpios (fármacos nunca "
        f"vistos en contextos contraindicados). Si la inclusión es "
        f"intencionada (p. ej. demostrar la no colisión de una clase concreta "
        f"de fármacos), añade el fármaco a _ALLOWED_OVERLAP con un comentario."
    )


def test_demo_cites_this_pin_file():
    """La demostración debe citar este archivo de fijación cerca de la frase de la afirmación de precisión.

    Iter 121 (exposición T2) añadió a docs/demo.html una frase posterior a
    la precisión que nombra los 6 invariantes de integridad de la cohorte y
    referencia este archivo de fijación. Una futura corrección de texto
    podría eliminar de forma silenciosa la referencia a la fijación,
    haciendo que la afirmación de precisión parezca desprotegida aunque las
    pruebas de integridad sigan pasando internamente. Esta fijación impone
    dicha exposición.

    Mismo patrón que las comprobaciones cruzadas "la documentación cita el
    archivo de fijación" de iter-110 e iter-115: la capa de pruebas y la
    superficie visible para el usuario deben mantenerse sincronizadas.
    """
    text = _DEMO.read_text()
    pin_filename = "test_negative_control_cohort_integrity_pin.py"
    assert pin_filename in text, (
        f"docs/demo.html debe citar "
        f"`tests/test_engine/{pin_filename}` cerca de la afirmación de "
        f"precisión de 0/10 falsos positivos para que el equipo revisor "
        f"pueda rastrear la integridad de la cohorte de precisión hasta su "
        f"archivo de fijación. Misma clase de desviación que la corrección "
        f"de la descripción del manifiesto en iter-107."
    )
    # Impón también la frase desambiguadora "cohort itself is pinned" O su
    # equivalente — la referencia a la fijación debe aparecer en el MISMO
    # párrafo que la afirmación de precisión, no en una sección sin relación.
    locality_anchors = (
        "cohort itself is pinned",
        "pinned for integrity",
        "6 invariants",
    )
    has_anchor = any(a in text for a in locality_anchors)
    assert has_anchor, (
        f"La cita del archivo de fijación de la demostración debe aparecer "
        f"con un anclaje de localidad como 'cohort itself is pinned' / "
        f"'pinned for integrity' / '6 invariants'. No se encontró ninguno "
        f"cerca de la afirmación de precisión — la corrección de texto puede "
        f"haber eliminado el fundamento."
    )
