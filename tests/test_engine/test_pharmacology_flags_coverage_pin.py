"""Verificación: la tabla de marcadores farmacológicos cubre cada fármaco del
caché + higiene de URL.

El nuevo `docs/pharmacology_flags.json` se publica y se rastrea en el
manifiesto, pero ninguna prueba garantiza lo siguiente:

  1. **Cobertura**: todo nombre de fármaco que aparezca en
     `docs/openevidence_cache.json` (drug_a o drug_b) debe tener una
     entrada correspondiente en pharmacology_flags. El crecimiento futuro
     del caché que añada un nuevo par de fármacos sin marcar el fármaco
     nuevo pasaría desapercibido para todas las pruebas existentes,
     rompiendo la afirmación de la tabla curada ("cada fármaco de nuestra
     cohorte se rastrea hasta clases farmacológicas con etiqueta de
     fármaco publicada").

  2. **Higiene de URL**: cada fármaco marcado debe tener ≥ 1 URL de
     evidencia, todas HTTPS. Mismo patrón que `test_cache_evidence_urls.py`
     pero aplicado a la tabla de marcadores.

  3. **Verificación de versión de esquema**: schema_version presente +
     flag_keys es una lista no vacía (esquema base).

Es la misma clase de deriva que las verificaciones de forma del caché
detectan para el propio caché, aplicada a su artefacto acompañante.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CACHE = _REPO_ROOT / "docs" / "openevidence_cache.json"
_FLAGS = _REPO_ROOT / "docs" / "pharmacology_flags.json"


def _cache_drug_set():
    cache = json.loads(_CACHE.read_text())
    drugs = set()
    for it in cache:
        drugs.add(it["drug_a"].lower().strip())
        drugs.add(it["drug_b"].lower().strip())
    return drugs


def _flags_doc():
    return json.loads(_FLAGS.read_text())


def test_every_cache_drug_has_pharmacology_flag_entry():
    """Todo nombre de fármaco del caché debe tener una entrada en
    `pharmacology_flags.json::drugs`. Las entradas con marcadores vacíos
    son aceptables (algunos fármacos genuinamente no tienen marcadores de
    clase farmacológica), pero el fármaco debe estar CATALOGADO para que
    una futura adición de marcador tenga dónde alojarse."""
    cache_drugs = _cache_drug_set()
    flagged = set(_flags_doc()["drugs"].keys())
    missing = cache_drugs - flagged
    assert not missing, (
        f"{len(missing)} fármacos del caché ausentes de pharmacology_flags: "
        f"{sorted(missing)[:8]}{'...' if len(missing) > 8 else ''}. "
        f"Añada cada uno a docs/pharmacology_flags.json con al menos "
        f"`flags: []` y una URL de evidencia de la etiqueta del fármaco."
    )


# Acota el conjunto de fármacos marcados huérfanos (fármacos marcados
# pero aún no referenciados por ninguna entrada del caché). Son entradas
# intencionales de preparación para el crecimiento futuro de la cohorte.
# La verificación permite que viva un conjunto pequeño y acotado, pero
# falla si los huérfanos se desvían más allá de la lista de permitidos
# documentada, evitando la acumulación de código muerto.
#
# Captura de deriva: tranylcypromine ELIMINADO de esta lista de
# permitidos porque el crecimiento de la cohorte incorporó
# tranylcypromine+venlafaxine (IMAO×IRSN, la 44.ª contraindicación).
# Ya no es huérfano. La lista de permitidos debe reflejar las entradas
# REALES de preparación, no notas históricas. Misma clase de deriva
# (la fuente única avanza, las constantes derivadas se quedan atrás).
_ALLOWED_ORPHAN_DRUGS = frozenset({
    # Clase AINE — preparado para el futuro crecimiento de cohorte con
    # ketorolac+litio o ketorolac+inhibidor de la ECA. La etiqueta del
    # fármaco para ketorolaco § 4 señala múltiples combinaciones de
    # alto riesgo.
    "ketorolac",
})


def test_orphan_flag_drugs_bounded_to_allowlist():
    """Los fármacos de `pharmacology_flags.json` que no tengan
    referencia en el caché deben estar en la lista de permitidos
    explícita `_ALLOWED_ORPHAN_DRUGS`. Las entradas intencionales de
    preparación están documentadas; los huérfanos accidentales de
    código muerto hacen fallar la verificación.

    Para añadir un nuevo huérfano (p. ej., tras una revisión previa que
    seleccionó un candidato pero que aún no llegó al caché), amplíe
    `_ALLOWED_ORPHAN_DRUGS` deliberadamente con la justificación.
    """
    cache_drugs = _cache_drug_set()
    flagged = set(_flags_doc()["drugs"].keys())
    orphans = flagged - cache_drugs
    unauthorized = orphans - _ALLOWED_ORPHAN_DRUGS
    assert not unauthorized, (
        f"{len(unauthorized)} entradas de fármaco marcado no tienen "
        f"referencia en el caché y NO están en la lista de permitidos "
        f"documentada: {sorted(unauthorized)}. Elimine el huérfano de "
        f"pharmacology_flags.json, O añádalo a `_ALLOWED_ORPHAN_DRUGS` "
        f"en este archivo de verificación con un comentario que explique "
        f"la justificación de la preparación (normalmente una revisión "
        f"previa)."
    )
    # Límite flexible: que la lista de permitidos no crezca sin límite.
    # Si se acumulan 10 o más huérfanos, es señal de que la cadencia de
    # crecimiento de la cohorte se está quedando atrás respecto a las
    # adiciones a la tabla de marcadores.
    assert len(orphans) <= 5, (
        f"{len(orphans)} fármacos marcados huérfanos superan el límite "
        f"flexible de 5. Ejecute una iteración de crecimiento de cohorte "
        f"para incorporar los candidatos preparados, o recorte la tabla de "
        f"marcadores para que coincida con el alcance de la cohorte activa."
    )


def test_every_flagged_drug_has_at_least_one_evidence_url():
    """La propuesta de la tabla curada es 'cada marcador se rastrea hasta
    una etiqueta de fármaco publicada o una referencia revisada por pares'.
    Una entrada sin URL rompe esa propuesta."""
    flags = _flags_doc()["drugs"]
    no_url = [n for n, e in flags.items() if not e.get("evidence_urls")]
    assert not no_url, (
        f"{len(no_url)} fármacos marcados no tienen URL de evidencia: "
        f"{no_url[:5]}{'...' if len(no_url) > 5 else ''}. "
        f"Cada entrada debe citar al menos una etiqueta de fármaco o "
        f"referencia revisada por pares."
    )


def test_every_evidence_url_is_https():
    """Las URL de HTTP plano en una cadena de auditoría de dominio
    regulado son un agujero de integridad silencioso. Misma verificación
    que test_cache_evidence_urls.py aplicada a la tabla de marcadores."""
    flags = _flags_doc()["drugs"]
    bad = []
    for name, e in flags.items():
        for u in e.get("evidence_urls", []):
            if not u.startswith("https://"):
                bad.append((name, u))
    assert not bad, (
        f"{len(bad)} URL de marcadores farmacológicos no son HTTPS. "
        f"Primera: {bad[0]}"
    )


def test_schema_version_and_flag_keys_present():
    """Esquema base: schema_version definido + flag_keys es una lista no
    vacía. Una iteración posterior elevó el mínimo de 12 a 25 (los 13
    marcadores base + 12 marcadores nuevos añadidos por el cierre de
    cobertura de explicación del 100 %)."""
    doc = _flags_doc()
    assert doc.get("schema_version"), (
        "pharmacology_flags.json debe incluir un campo schema_version"
    )
    flag_keys = doc.get("flag_keys", [])
    assert isinstance(flag_keys, list) and len(flag_keys) >= 25, (
        f"flag_keys debe ser una lista no vacía de >= 25 entradas; "
        f"se obtuvo {len(flag_keys) if isinstance(flag_keys, list) else type(flag_keys).__name__}. "
        f"Se añadieron 12 nuevas clases de marcador para el cierre de "
        f"cobertura del 100 %; eliminar cualquiera de ellas hace retroceder "
        f"silenciosamente la afirmación de la tabla curada."
    )


def test_every_flag_key_is_canonical_snake_case_is_prefix():
    """Todo marcador de `flag_keys` DEBE empezar por `is_` y estar en
    snake_case. Incumplir esto rompe los codificadores posteriores que
    hacen coincidencia de patrón sobre el prefijo (p. ej.
    `_pair_derived_flags` comprueba
    `has_pair("is_cyp3a4_strong_inhibitor", "is_cyp3a4_substrate")`)."""
    flag_keys = _flags_doc()["flag_keys"]
    snake = re.compile(r"^is_[a-z][a-z0-9_]*$")
    bad = [k for k in flag_keys if not snake.match(k)]
    assert not bad, (
        f"clave(s) de marcador no canónica(s): {bad}. "
        f"Todas las claves deben coincidir con `is_[a-z][a-z0-9_]*`."
    )


def test_drug_names_in_flag_table_are_canonicalised_lowercase():
    """Las claves de nombre de fármaco deben estar en minúsculas y con
    espacios en blanco colapsados para que la búsqueda con
    `_flag_bits(drug_name)` sea coherente con la canonicalización de
    `drug_a` / `drug_b` del caché."""
    flags = _flags_doc()["drugs"]
    bad = [n for n in flags.keys() if n != " ".join(n.lower().split())]
    assert not bad, (
        f"claves de nombre de fármaco no canónicas: {bad[:5]}. "
        f"Cada clave debe ser igual a `' '.join(name.lower().split())`."
    )


# ─── Verificaciones de nombre canónico de clave de marcador + regla ─────

# Las 13 clases de marcador base.
_BASELINE_FLAG_KEYS = frozenset({
    "is_cyp3a4_strong_inhibitor",
    "is_cyp3a4_substrate",
    "is_cyp2c9_inhibitor",
    "is_cyp2d6_inhibitor",
    "is_p_gp_inhibitor",
    "is_p_gp_substrate",
    "is_oatp1b1_inhibitor",
    "is_statin",
    "is_anticoagulant",
    "is_maoi",
    "is_serotonergic",
    "is_nsaid",
    "is_pde5_inhibitor",
})

# Las 12 adiciones posteriores (los marcadores del cierre de cobertura
# del 100 %).
_ITER140_FLAG_KEYS = frozenset({
    "is_iodinated_contrast",
    "is_metformin",
    "is_renal_state",
    "is_cyp1a2_inhibitor",
    "is_cyp1a2_substrate",
    "is_xanthine_oxidase_inhibitor",
    "is_thiopurine",
    "is_folate_antagonist",
    "is_tetracycline",
    "is_retinoid",
    "is_ace_inhibitor",
    "is_neprilysin_inhibitor",
})


def test_baseline_flag_keys_present():
    """Las 13 clases de marcador base deben permanecer en `flag_keys`.
    Renombrar cualquiera de ellas rompe silenciosamente las búsquedas de
    regla de `_pair_derived_flags` (la regla 0 llama a
    `has_pair('is_cyp3a4_strong_inhibitor', 'is_cyp3a4_substrate')`)."""
    flag_keys = set(_flags_doc()["flag_keys"])
    missing = _BASELINE_FLAG_KEYS - flag_keys
    assert not missing, (
        f"{len(missing)} clase(s) de marcador base ausente(s) de "
        f"pharmacology_flags.json::flag_keys: {sorted(missing)}. "
        f"Estos nombres se referencian en las reglas 0-5 de "
        f"`_pair_derived_flags` y un renombrado haría retroceder "
        f"silenciosamente 6 de las 13 reglas."
    )


def test_iter140_flag_keys_present():
    """Las 12 clases de marcador añadidas para el cierre de cobertura del
    100 % deben permanecer en `flag_keys`. Renombrar cualquiera hace
    retroceder silenciosamente la verificación de cobertura del 100 % al
    71,4 % (la línea base anterior)."""
    flag_keys = set(_flags_doc()["flag_keys"])
    missing = _ITER140_FLAG_KEYS - flag_keys
    assert not missing, (
        f"{len(missing)} clase(s) de marcador adicional(es) ausente(s) de "
        f"pharmacology_flags.json::flag_keys: {sorted(missing)}. "
        f"Estos marcadores cierran la clase documentada de 8 mecanismos "
        f"para la cobertura de explicación del 100 %; eliminar cualquiera "
        f"de ellos hace retroceder silenciosamente la cobertura al 71,4 %."
    )


# Ejemplo canónico: cada índice de regla derivada de par → (drug_a, drug_b)
# del caché activo que DEBE disparar esa regla. Detecta tanto
#   (a) una regla que queda inactiva (su ejemplo nombrado deja de
#       dispararla) como
#   (b) una edición de la tabla de marcadores que elimina el marcador del
#       ejemplo.
_RULE_CANONICAL_EXAMPLES = (
    # (idx, drug_a,        drug_b,           rule_name)
    (0,  "clarithromycin", "simvastatin",    "cyp3a4_inhib_substrate"),
    (1,  "gemfibrozil",    "simvastatin",    "oatp1b1_inhib_statin"),
    (2,  "clarithromycin", "simvastatin",    "p_gp_inhib_substrate"),
    # regla 3 (cyp2c9_inhib_anticoag): aún no ejercida por ninguna entrada
    # de contraindicación del caché — marcador de posición para futuras
    # adiciones de warfarina+CYP2C9-fuerte. Omitida de la verificación de
    # ejemplo canónico hasta que se pueble.
    (4,  "phenelzine",     "sertraline",     "maoi_serotonergic"),
    (5,  "isosorbide mononitrate", "sildenafil", "pde5_nitrate"),
    (6,  "iodine",         "metformin",      "iodinated_contrast_metformin"),
    (7,  "ciprofloxacin",  "tizanidine",     "cyp1a2_inhib_substrate"),
    (8,  "allopurinol",    "azathioprine",   "xo_thiopurine"),
    (9,  "methotrexate",   "trimethoprim-sulfamethoxazole", "folate_antagonist_pair"),
    (10, "doxycycline",    "isotretinoin",   "tetracycline_retinoid"),
    (11, "lisinopril",     "sacubitril",     "ace_neprilysin"),
    (12, "metformin",      "renal impairment", "metformin_renal"),
)


def test_each_rule_fires_on_canonical_example_pair():
    """Para cada regla derivada de par que tenga un ejemplo de
    contraindicación en el caché, comprueba que el par de ejemplo canónico
    dispara esa regla. Detecta la clase de regresión silenciosa:
      (a) una regla desaparece (el par de ejemplo deja de dispararla) —
          la verificación de cobertura del 100 % aún podría pasar si otra
          regla cubre el ejemplo, pero la regla está inactiva;
      (b) una edición de la tabla de marcadores elimina el marcador del
          fármaco de ejemplo — la regla sigue funcionando en el código
          pero pierde su único ejemplo conocido en el caché.

    Mismo patrón de verificación de ejemplo canónico que el conjunto
    canónico de 9 reglas de arch-mind + la forma del caché.
    """
    import importlib
    import sys
    sys.path.insert(0, str(_REPO_ROOT / "retrain_runpod"))
    import train_bitnet_v3_atc
    importlib.reload(train_bitnet_v3_atc)
    pair_derived = train_bitnet_v3_atc._pair_derived_flags

    failures = []
    for idx, da, db, name in _RULE_CANONICAL_EXAMPLES:
        flags = pair_derived(da, db)
        assert len(flags) == 13, (
            f"_pair_derived_flags devolvió {len(flags)} bits "
            f"(se esperan 13)."
        )
        if flags[idx] != 1:
            fired = [i for i, f in enumerate(flags) if f]
            failures.append(
                f"regla {idx} ({name}) — el ejemplo canónico "
                f"{da!r} + {db!r} NO disparó la regla {idx}; "
                f"reglas disparadas en realidad: {fired}"
            )
    assert not failures, (
        "Regresión(es) de ejemplo canónico:\n  "
        + "\n  ".join(failures)
        + "\nRestaure el/los marcador(es) ausente(s) en los fármacos de "
          "ejemplo o actualice _RULE_CANONICAL_EXAMPLES con un nuevo par "
          "representativo."
    )
