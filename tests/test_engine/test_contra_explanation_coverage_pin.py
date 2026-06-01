"""Fijación: cada entrada contraindicada de la caché tiene o bien un
indicador de regla de interacción (DDI) derivado de par que se dispara, O
BIEN está documentada explícitamente como un mecanismo no cubrible por
indicador (p. ej. antagonismo del folato, inhibición de la xantina-oxidasa).

Iter 105 (rigor de evaluación): la tabla de indicadores ATC de la iter-96 +
la fijación de cobertura de la iter-100 garantizan que cada fármaco esté
*catalogado*, pero no dicen nada sobre si la tabla curada puede *explicar*
una decisión de contraindicación. Quien ejecuta `verify_audit_replay.py` ve
"este par está contraindicado, la repetición de auditoría lo reproduce", pero
no tiene una explicación legible por máquina del PORQUÉ.

Auditoría de la iter 105 (22 contraindicaciones, 6 clases de indicador
derivado de par):

  Cobertura   14/22 = 63,6%      (con al menos un indicador derivado de par)
  Hueco        8/22              mecanismos sin indicador

Mecanismos de hueco documentados (8 contraindicaciones):
  - contraste yodado / yodo + metformina  — acidosis láctica (renal)
  - ciprofloxacino + tizanidina           — inhibición de CYP1A2
  - metformina + insuficiencia renal      — por comorbilidad
  - alopurinol + azatioprina              — xantina-oxidasa (XO)
  - metotrexato + trimetoprima-sulfa      — antagonismo del folato
  - doxiciclina + isotretinoína           — pseudotumor cerebral
  - lisinopril + sacubitrilo              — bradicinina / angioedema

La fijación comprueba:

  1. Cobertura en vivo ≥ 60% (el piso detecta una regresión que rompa el
     disparo de un indicador existente).
  2. Toda contraindicación sin cobertura de indicador está en la lista de
     huecos documentados (una NUEVA contraindicación sin cobertura activa la
     comprobación: el operador debe o bien añadir un nuevo indicador derivado
     de par O BIEN añadir explícitamente el nuevo mecanismo a la lista de
     huecos con un comentario que lo justifique).

Esto fija la disciplina de "honestidad sobre los huecos" que ha sido el sello
del bucle (cf. el hueco del invariante de federación de la iter 22, el
verificador de repetición de auditoría de la iter 80, la corrección de
confusión de precisión de la iter 102).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "retrain_runpod"))


def _pair_derived(da: str, db: str) -> list[int]:
    """Reimporta en cada llamada para captar actualizaciones de la tabla de
    indicadores."""
    # Recarga la función auxiliar, ya que cachea el JSON al importarse.
    import importlib
    import train_bitnet_v3_atc as t
    importlib.reload(t)
    return t._pair_derived_flags(da, db)


def _contras() -> list[dict]:
    cache = json.loads((_REPO_ROOT / "docs" / "openevidence_cache.json").read_text())
    return [it for it in cache if it["severity"] == "contraindicated"]


# Mecanismos NO cubiertos aún por los 6 indicadores derivados de par de la
# iter-96. Una NUEVA contraindicación que caiga en una de estas clases de
# hueco existentes se acepta (emparejada por par). Una NUEVA contraindicación
# en una NUEVA clase de hueco (algo que la tabla curada no puede explicar)
# falla la comprobación: el operador debe o bien añadir una nueva clase de
# indicador derivado de par o bien añadir el nuevo mecanismo a esta lista con
# una razón documentada.
_DOCUMENTED_GAP_PAIRS = frozenset()  # iter-140: vacía — se logró cobertura del 100%.
# Lista anterior a la iter-140 (ahora toda cubierta por nuevas reglas derivadas de par):
#   ("contrast dye", "metformin")            -> iodinated_contrast × metformin (regla 6)
#   ("iodine", "metformin")                  -> iodinated_contrast × metformin (regla 6)
#   ("ciprofloxacin", "tizanidine")          -> cyp1a2 inhib × substrate (regla 7)
#   ("metformin", "renal impairment")        -> metformin × renal-state (regla 12)
#   ("allopurinol", "azathioprine")          -> xanthine-oxidase × thiopurine (regla 8)
#   ("methotrexate", "trimethoprim-sulfa...") -> folate-antagonist pair (regla 9)
#   ("doxycycline", "isotretinoin")          -> tetracycline × retinoid (regla 10)
#   ("lisinopril", "sacubitril")             -> ACE × neprilysin (regla 11)


def _canonical_pair(da: str, db: str) -> tuple[str, str]:
    return tuple(sorted((da.lower().strip(), db.lower().strip())))


def test_contra_explanation_coverage_at_or_above_floor():
    """La cobertura en vivo de los indicadores derivados de par sobre las
    contraindicadas debe ser del 100%. La línea base de la iter-105 era del
    60% (con 8 mecanismos de hueco documentados); la iter-140 cerró el hueco
    añadiendo 7 nuevas reglas derivadas de par (iodinated_contrast×metformin,
    CYP1A2 inhib×substrate, xanthine-oxidase×thiopurine, folate-antagonist×
    folate-antagonist, tetracycline×retinoid, ACE×neprilysin, metformin×
    renal-state). Cada entrada contraindicada de la caché ahora se rastrea
    hasta una regla farmacológica curada.
    """
    contras = _contras()
    covered = 0
    uncovered_pairs = []
    for it in contras:
        flags = _pair_derived(it["drug_a"], it["drug_b"])
        if any(flags):
            covered += 1
        else:
            uncovered_pairs.append((it["drug_a"], it["drug_b"]))
    coverage = covered / len(contras)
    assert coverage >= 1.0, (
        f"La cobertura de indicadores derivados de par sobre las "
        f"contraindicadas bajó a {coverage:.1%} ({covered}/{len(contras)}); "
        f"el piso de la iter-140 es 100%. Par(es) sin cubrir: {uncovered_pairs}. "
        f"O bien restaure la clase de indicador eliminada o bien amplíe "
        f"`_pair_derived_flags` con una nueva regla para el nuevo mecanismo."
    )


def test_uncovered_contras_are_in_documented_gap_list():
    """Toda contraindicación que no dispare NINGÚN indicador derivado de par
    debe estar en la lista de huecos documentados. Una NUEVA contraindicación
    sin cubrir (p. ej. una nueva clase de mecanismo añadida a la caché sin un
    indicador correspondiente) falla la comprobación.

    Respuesta del operador cuando esto se dispara:
      (a) Añadir una nueva clase de indicador derivado de par para tratar el
          mecanismo (preferido: amplía la tabla curada), O BIEN
      (b) Añadir el nuevo par a _DOCUMENTED_GAP_PAIRS en esta prueba con un
          comentario que explique por qué la tabla curada no puede cubrirlo.
    """
    contras = _contras()
    uncovered_undocumented = []
    for it in contras:
        flags = _pair_derived(it["drug_a"], it["drug_b"])
        if any(flags):
            continue
        pair = _canonical_pair(it["drug_a"], it["drug_b"])
        if pair not in _DOCUMENTED_GAP_PAIRS:
            uncovered_undocumented.append(pair)
    assert not uncovered_undocumented, (
        f"{len(uncovered_undocumented)} nuevo(s) par(es) contraindicado(s) "
        f"carece(n) tanto de un indicador derivado de par COMO de una entrada "
        f"en _DOCUMENTED_GAP_PAIRS: {uncovered_undocumented}. O bien añada una "
        f"nueva clase de indicador a "
        f"retrain_runpod/train_bitnet_v3_atc.py::_pair_derived_flags O BIEN "
        f"añada el par a _DOCUMENTED_GAP_PAIRS en esta prueba con un "
        f"comentario que explique el mecanismo."
    )


def test_documented_gap_pairs_remain_in_cache():
    """Si un par sale de la caché (p. ej. reestructuración de cohorte),
    elimínelo de _DOCUMENTED_GAP_PAIRS para que la lista no quede obsoleta.
    Esta prueba se dispara cuando un par de hueco documentado ya no está en la
    caché, obligando al operador a limpiar la lista.

    La iter-140 dejó _DOCUMENTED_GAP_PAIRS vacía (cobertura del 100%); esta
    prueba permanece como salvaguarda para regresiones futuras en las que
    alguien vuelva a añadir un par a la lista de huecos Y el par salga de la
    caché.
    """
    contras = _contras()
    cache_pairs = {_canonical_pair(it["drug_a"], it["drug_b"]) for it in contras}
    stale = [p for p in _DOCUMENTED_GAP_PAIRS if p not in cache_pairs]
    assert not stale, (
        f"{len(stale)} par(es) de hueco documentado ya no está(n) en la "
        f"caché: {stale}. Elimínelo(s) de _DOCUMENTED_GAP_PAIRS: ahora son "
        f"curiosidades históricas."
    )


def test_pair_derived_flags_returns_thirteen_rules():
    """Fijación de la extensión de la iter-140: _pair_derived_flags debe
    devolver 13 bits de indicador (eran 6 antes de la iter-140). Esto bloquea
    el tamaño del conjunto de reglas para que un mantenedor futuro que elimine
    una de las 7 reglas nuevas (reglas 6-12, que cubren iodinated-contrast×
    metformin, CYP1A2, xanthine-oxidase×thiopurine, par folate-antagonist,
    tetracycline×retinoid, ACE×neprilysin, metformin×renal-state) rompa esta
    prueba antes de que la cobertura del 100% regrese en silencio al 71,4%.
    """
    flags = _pair_derived("acetaminophen", "ibuprofen")  # par seguro arbitrario
    assert len(flags) == 13, (
        f"_pair_derived_flags devolvió {len(flags)} bits; la iter-140 "
        f"bloqueó el conjunto de reglas en 13 (las 6 originales + 7 "
        f"extensiones de la iter-140 para cerrar la clase de hueco "
        f"documentado). Restaure la(s) regla(s) faltante(s) o actualice esta "
        f"fijación."
    )
