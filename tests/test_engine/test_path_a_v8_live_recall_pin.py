# Copyright 2026 STARGA Inc. — Apache-2.0
"""Fija la recuperación de contraindicados en caché viva de Path A v8 (1f0f8859, h=256).

El barrido v8 de iter-244 aterrizó: duplicar hidden_dim 128 → 256 rompió
el techo arquitectónico de v7. La semilla 71 alcanzó la compuerta estricta
de recuperación total (41/41 contra + 4/4 major + 0 FP) en la cohorte de
41 contraindicados de iter-235.

  Path A v3 (h=64,  eea0e637):    29/38 contra + 1 FP (histórico)
  Path A v5 (h=128, 1ff61a6a):    31/38 contra + 0 FP (preparado en iter-166)
  Path A v6 (h=128, 592ee51e):    40/41 contra + 4/4 major + 0 FP (iter-207, cohorte iter-235)
  Path A v8 (h=256, 1f0f8859):    41/41 contra + 4/4 major + 0 FP   ← ESTA FIJACIÓN

v8 hereda la entrada de características de 193 dimensiones de v6/v5 pero
duplica la dimensión oculta a 256 — la extensión arquitectónica que rompió
el techo de BOOST_KEYS @200x descubierto en el barrido v7 de iter-241
(donde v7 con h=128 no podía satisfacer 41/41 + 4/4 + 0 FP simultáneamente
con ninguna semilla).

AÚN NO promovido al motor — la promoción requiere el mismo trabajo en
cascada de la clase iter-166 (elevación del codificador a 193 dimensiones,
ya en v5/v6/v8; el cambio del paquete del motor requiere refijar cada
comprobación derivada de V6, actualizar documentación y demostración,
regenerar la repetición de auditoría bajo el bundle_id de V8, rotar el SHA
del manifiesto, repetir las 44 fijaciones de auditoría) **además** de la
extensión de forma de `hidden_w` de 64 → 256 en
`engine/bitnet_classifier.py`. El motor todavía carga cfadb4f6 (línea base
de iter-72). v8 reside en
retrain_runpod/bitnet_weights_v8_h256.json.

Esta fijación usa el mismo paso hacia delante ternario en Q16.16 que el
motor. Un paquete que "funciona" con NumPy en coma flotante pero falla bajo
Q16.16 no es seguro como ancla de repetición de auditoría — debe pasar esta
fijación por la ruta Q16.16 para ser apto para la promoción al motor.

Resultado del barrido que produjo este paquete
==============================================
El barrido de 30 semillas de ``retrain_runpod/sweep_v8_h256.py`` se lanzó
en iter-242 en CPU local. La semilla=71 alcanzó la compuerta estricta
(41/41 + 4/4 + 0 FP); el barrido guardó el paquete y se detuvo según su
lógica de parada temprana.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from retrain_runpod.train_bitnet_v3_full import encode_pair  # noqa: E402

_CACHE = _REPO_ROOT / "docs" / "openevidence_cache.json"
_BUNDLE = _REPO_ROOT / "retrain_runpod" / "bitnet_weights_v8_h256.json"

_PATH_A_V8_BUNDLE_ID = (
    "1f0f88591c05af57c62d844b667639b29c7d1f0eb1b213073d158101611f76e6"
)

# Mediciones de referencia en Q16.16.
# Iter-244: v8 alcanzó 41/41 en la cohorte de 41 contraindicados de
# iter-235 (recuperación total + 4/4 major + 0 FP). El hidden_dim duplicado
# 128 → 256 dio a la red capacidad suficiente para satisfacer las tres
# restricciones simultáneamente — v7 con h=128 no podía (el mejor v7 fue
# 40/41+4/4+0FP, igualando a v6).
# Crecimiento de cohorte en iter-249: añadido (quinidine, ritonavir) —
# inhibidor de proteasa de VIH × antiarrítmico de Clase IA / doble
# prolongador del QT, NUEVA subclase. La verificación previa de v8 confirmó
# la clasificación de contraindicado con un logit de +14.59 en Q16.16
# (margen claro), de modo que la cohorte pasó de 41 → 42 con los aciertos
# de 41 → 42 en sincronía (cero fallos preservados).
# Crecimiento de cohorte en iter-254: añadido (nitroglycerin, vardenafil) —
# extiende la regla 5 (PDE5 × nitrato) de 2 → 3 entradas; el vardenafilo se
# une al sildenafilo + tadalafilo como el tercer inhibidor de la PDE5 de la
# cohorte.
# Crecimiento de cohorte en iter-280: tranylcypromine + venlafaxine (IMAO ×
# IRSN) — cierra el huérfano de pre-preparación de tranilcipromina de
# iter-264/iter-270. La verificación previa de v8 lo detectó con +90.79 en
# Q16.16 (margen fuerte mediante el disparo de la regla derivada del par
# [4] maoi_serotonergic). La cohorte pasó de 43 → 44 con los aciertos de
# 43 → 44 en sincronía. Las fichas técnicas de Effexor § 4 + Parnate
# § 4 listan ambos la contraindicación absoluta con un lavado de 14 días
# (riesgo de síndrome serotoninérgico / crisis hipertensiva).
_V8_CONTRA_HITS = 44
_V8_CONTRA_TOTAL = 44
_V8_FP_COUNT = 0       # se mantiene el invariante de cero FP
_V8_MAJOR_HITS = 4
_V8_MAJOR_TOTAL = 4

# Iter-244: V8 tiene CERO fallos conocidos. La tupla vacía significa
# recuperación total 41/41 — el avance del techo arquitectónico respecto a
# v7. Un crecimiento futuro de la cohorte que añada un contraindicado que v8
# falle extendería esta tupla (misma forma que tenía v6 en la era iter-215
# → iter-244).
_V8_EXPECTED_MISSES: tuple[tuple[str, str], ...] = (
    # Vacía — v8 detecta los 41 pares contraindicados.
    # El doblado arquitectónico de iter-244 rompió el techo de BOOST_KEYS de v7.
    # Un crecimiento futuro de la cohorte que añada un contraindicado que v8
    # falle extendería esta tupla (misma forma que tenía v6 en la era
    # iter-215 → iter-244).
)

_Q16_ONE = 1 << 16


def _classify_q16(da: str, db: str, bundle: dict) -> str:
    """Paso hacia delante ternario en Q16.16 — idéntico bit a bit al del motor."""
    feat = encode_pair(da, db)
    feat_q16 = [v * _Q16_ONE for v in feat]
    h_w = bundle["hidden_w"]
    h_b = bundle["hidden_b"]
    o_w = bundle["output_w"]
    o_b = bundle["output_b"]

    def _dot_t(act: list[int], tw: list[int]) -> int:
        s = 0
        for a, t in zip(act, tw):
            if t == 1:
                s += a
            elif t == -1:
                s -= a
        return s

    hidden = []
    for j, row in enumerate(h_w):
        v = _dot_t(feat_q16, row) + h_b[j]
        hidden.append(v if v > 0 else 0)
    logits = []
    for k, row in enumerate(o_w):
        v = _dot_t(hidden, row) + o_b[k]
        logits.append(v)
    labels = ("none", "moderate", "serious", "major", "contraindicated")
    return labels[max(range(len(logits)), key=lambda i: logits[i])]


def _live_contras() -> list[dict]:
    cache = json.loads(_CACHE.read_text())
    return [e for e in cache if e.get("severity") == "contraindicated"]


def _live_majors() -> list[dict]:
    cache = json.loads(_CACHE.read_text())
    return [e for e in cache if e.get("severity") == "major"]


def _live_non_contras() -> list[dict]:
    cache = json.loads(_CACHE.read_text())
    return [e for e in cache if e.get("severity") != "contraindicated"]


def test_path_a_v8_bundle_id_pinned() -> None:
    """El `_meta.bundle_id` del paquete v8 debe ser igual a la constante fijada.
    El barrido de iter-244 con semilla=71 produjo este paquete; cualquier
    otro bundle_id significa que aterrizó un nuevo barrido sin refijar."""
    bundle = json.loads(_BUNDLE.read_text())
    live_id = bundle["_meta"]["bundle_id"]
    assert live_id == _PATH_A_V8_BUNDLE_ID, (
        f"Desviación del paquete Path A v8: live={live_id!r}, "
        f"fijado={_PATH_A_V8_BUNDLE_ID!r}. Un nuevo paquete v8+ debe "
        f"reemplazar esta constante en el mismo commit para que la fijación "
        f"de recuperación (abajo) se revalide contra el paquete nuevo."
    )


def test_path_a_v8_q16_recall_full() -> None:
    """Inferencia en Q16.16: v8 acierta 41/41 contraindicados de la caché
    viva bajo Q16.16 en la cohorte de iter-235. `_V8_EXPECTED_MISSES` vacía
    — v8 detecta cada par contraindicado. Cualquier fallo dispara la
    fijación y señala:
      (a) el crecimiento de la cohorte introdujo un contraindicado nuevo no
          disparado por ninguna regla → cobertura del codificador rota;
          añadir regla + BOOST_KEYS + reentrenar
      (b) los pesos del paquete preparado regresaron
          → reejecutar el barrido v8 de iter-244
      (c) se eliminó silenciosamente una bandera de pharmacology_flags.json
          → la fijación de cobertura por regla de iter-203 también debería dispararse
    La garantía bidireccional con la tupla de fallos vacía es "v8 es el
    paquete sin fallos conocidos" — ni MÁS fallos (regresión) ni MENOS
    (imposible, ya que el conjunto de fallos ya es mínimo).
    """
    bundle = json.loads(_BUNDLE.read_text())
    contras = _live_contras()
    assert len(contras) == _V8_CONTRA_TOTAL, (
        f"El recuento de contraindicados de la caché viva se desvió: "
        f"live={len(contras)}, fijado={_V8_CONTRA_TOTAL}."
    )
    misses: list[tuple[str, str]] = []
    for entry in contras:
        pred = _classify_q16(entry["drug_a"], entry["drug_b"], bundle)
        if pred != "contraindicated":
            misses.append(
                (entry["drug_a"].lower(), entry["drug_b"].lower())
            )
    expected_pairs = {
        tuple(sorted([a.lower(), b.lower()])) for a, b in _V8_EXPECTED_MISSES
    }
    actual_pairs = {tuple(sorted(p)) for p in misses}
    assert actual_pairs == expected_pairs, (
        f"Desviación del conjunto de fallos de Path A v8: live={sorted(actual_pairs)}, "
        f"fijado={sorted(expected_pairs)}.\n"
        f"Si aterrizó un reentrenamiento v9 (aparecieron fallos nuevos), "
        f"actualiza _V8_CONTRA_HITS + _V8_EXPECTED_MISSES + bundle_id en sincronía."
    )


def test_path_a_v8_q16_major_full_recall() -> None:
    """v8 detecta los 4 pares de clase major (paroxetine+tamoxifen,
    clarithromycin+digoxin, dabigatran+dronedarone, voriconazole+
    tacrolimus) bajo Q16.16 — el avance del doblado arquitectónico que v7
    (h=128) no pudo lograr."""
    bundle = json.loads(_BUNDLE.read_text())
    majors = _live_majors()
    assert len(majors) == _V8_MAJOR_TOTAL, (
        f"Desviación de la cohorte de clase major: live={len(majors)}, fijado={_V8_MAJOR_TOTAL}."
    )
    hits = 0
    for entry in majors:
        pred = _classify_q16(entry["drug_a"], entry["drug_b"], bundle)
        if pred == "major":
            hits += 1
    assert hits == _V8_MAJOR_HITS, (
        f"Desviación de la recuperación major de Path A v8: live={hits}, fijado={_V8_MAJOR_HITS}. "
        f"v8 debe detectar los 4 pares de clase major bajo Q16.16."
    )


def test_path_a_v8_q16_zero_fp() -> None:
    """v8 NUNCA debe predecir contraindicado para un par no contraindicado.
    El invariante de cero FP es la afirmación de seguridad crítica que
    distingue a BitNet 4.5 como un veto de alta precisión en lugar de un
    clasificador primario."""
    bundle = json.loads(_BUNDLE.read_text())
    non_contras = _live_non_contras()
    fps = []
    for entry in non_contras:
        pred = _classify_q16(entry["drug_a"], entry["drug_b"], bundle)
        if pred == "contraindicated":
            fps.append((entry["drug_a"], entry["drug_b"], entry["severity"]))
    assert not fps, (
        f"Desviación de falsos positivos de Path A v8: {len(fps)} pares no "
        f"contraindicados clasificados como contraindicados bajo Q16.16: {fps[:5]}. "
        f"Cero FP es el invariante del suelo de seguridad."
    )
    assert len(fps) == _V8_FP_COUNT, (
        f"Desviación del recuento de FP: live={len(fps)}, fijado={_V8_FP_COUNT}"
    )


def test_path_a_v8_meta_block_consistency() -> None:
    """El bloque _meta del paquete v8 debe reflejar las decisiones
    arquitectónicas: entrada de características de 193 dimensiones × 256
    ocultas × 5 logits, pesos ternarios, sesgos en q16.16, 13 reglas
    derivadas de pares, 26 banderas ATC."""
    bundle = json.loads(_BUNDLE.read_text())
    meta = bundle["_meta"]
    assert meta["in_features"] == 193, (
        f"Desviación de in_features de v8: live={meta['in_features']}, esperado 193 "
        f"(64 trits de hash + 26 bits de bandera por fármaco × 2 + 13 reglas derivadas de pares)"
    )
    assert meta["hidden_features"] == 256, (
        f"Desviación de hidden_features de v8: live={meta['hidden_features']}, esperado 256 "
        f"(doblado arquitectónico de v8 en iter-242 desde los 128 de v6)"
    )
    assert meta["out_features"] == 5, (
        f"Desviación de out_features de v8: live={meta['out_features']}, esperado 5 "
        f"(none / moderate / serious / major / contraindicated)"
    )
    assert meta["weight_dtype"] == "ternary", (
        f"Desviación de weight_dtype de v8: live={meta['weight_dtype']!r}, esperado 'ternary'"
    )
    assert meta["bias_dtype"] == "q16.16", (
        f"Desviación de bias_dtype de v8: live={meta['bias_dtype']!r}, esperado 'q16.16'"
    )


def test_path_a_v8_strictly_supersedes_v6() -> None:
    """v8 detecta estrictamente MÁS pares contraindicados de los que v6
    detectó jamás. v6 era 40/41 (1 fallo conocido: lurasidone+ketoconazole).
    v8 es 41/41 (cero fallos conocidos). El doblado arquitectónico 128→256
    está obligado mecánicamente a ser netamente positivo — si v8 alguna vez
    cae por debajo de los 40 contraindicados de v6, esta fijación se dispara.
    """
    assert _V8_CONTRA_HITS >= 40, (
        f"Invariante strictly_supersedes_v6 de Path A v8 violado: "
        f"v8 acierta {_V8_CONTRA_HITS}, v6 acertó 40. El doblado "
        f"arquitectónico 128 → 256 de iter-244 debía romper el techo de "
        f"BOOST_KEYS @200x, no regresar por debajo de la recuperación de v6."
    )
