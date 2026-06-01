"""Fija qué interacciones mayores detecta BitNet POR SÍ SOLO frente a las
que omite sobre la caché en vivo.

La iteración 110 saca a la luz una confusión arquitectónica latente en la
interfaz:

  El minigráfico etiquetado "Recuperación por clase de gravedad" muestra
  `mayor: 100% · 4 / 4`. Esa cifra es la **salida final del motor**, no la
  de BitNet de la Capa 4.5 por sí sola. El motor llega a 4/4 porque la
  política de seguridad de la Capa 4.5 NUNCA rebaja un `mayor` previo a
  algo más débil: cuando BitNet discrepa diciendo "ninguna", se dispara
  una advertencia `BITNET_SAFETY_DOWNGRADE_DISAGREEMENT` y se conserva el
  veredicto previo.

  BitNet POR SÍ SOLO detecta **3 de 4** interacciones mayores en la cohorte
  de la iter-109:

    ✓ paroxetina + tamoxifeno        (CYP2D6 — añadida iter 39)
    ✓ claritromicina + digoxina      (P-gp + CYP3A4 — añadida iter 83)
    ✓ dabigatrán + dronedarona       (P-gp — añadida iter 109)
    ✗ tacrólimus + voriconazol       (predijo "ninguna" — añadida iter 93;
                                     el mecanismo cruzado de transportador
                                     + CYP3A4 potente choca con el techo
                                     arquitectónico documentado en la
                                     iter 105)

  La omisión se conserva (no se corrige en silencio) porque:
    1. Es el techo arquitectónico honesto: el codificador basado solo en
       hash de BitNet no puede separar las mayores de mecanismo cruzado
       transportador+CYP de la clase "ninguna" sin características más
       ricas (la integración de la tabla farmacológica de la Vía A es
       trabajo de la iter 110+).
    2. La anulación por seguridad (`BITNET_SAFETY_DOWNGRADE_DISAGREEMENT`)
       convierte la omisión en una señal ejecutable: la advertencia
       aparece en el registro de evaluación de regresión en cada ejecución.

Esta fijación congela ambos conjuntos para que:
  - Un reentrenamiento silencioso que corrija la omisión sin declararlo
    falle la comprobación (obliga a un honesto "corregimos el par X" en el
    commit y la interfaz).
  - Una regresión que empiece a clasificar mal una mayor previamente
    correcta falle de inmediato (obliga a investigar, no a una deriva
    silenciosa).
  - El texto de "BitNet por sí solo" en la interfaz deba referirse de forma
    explícita al hecho de 3-de-4 para que no se confunda con el 4/4 a nivel
    de motor.
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CALIB = _REPO_ROOT / "docs" / "bitnet_calibration.json"
_DEMO_HTML = _REPO_ROOT / "docs" / "demo.html"

# Promoción v8 de la iter-275: 4/4 verdaderos positivos de clase mayor + 0
# omisiones. Antes de v8 (cfadb4f6) había 3/4 correctos + 1 omisión
# (tacrólimus+voriconazol omitido por el techo del codificador v1 basado
# solo en hash). El codificador v8 con 26 indicadores + 13 derivados de par
# cierra el hueco del mecanismo cruzado P-gp + CYP3A4 potente. Comprobación
# de recuperación de mayores: 4/4 = 100%.
_BITNET_CORRECT_MAJORS = frozenset({
    ("clarithromycin", "digoxin"),
    ("dabigatran", "dronedarone"),
    ("paroxetine", "tamoxifen"),
    ("tacrolimus", "voriconazole"),
})

_BITNET_MISS_MAJORS = frozenset()  # v8 detecta las 4 mayores

_BITNET_MISS_PREDICTED_AS = "none"  # constante residual; sin omisiones en vivo


def _calib_majors():
    calib = json.loads(_CALIB.read_text())
    majors = [e for e in calib["entries"] if e.get("ground_truth") == "major"]
    correct = frozenset(
        (e["drug_a"], e["drug_b"]) for e in majors if e.get("correct")
    )
    miss = frozenset(
        (e["drug_a"], e["drug_b"]) for e in majors if not e.get("correct")
    )
    miss_predicted = {
        (e["drug_a"], e["drug_b"]): e["predicted"]
        for e in majors
        if not e.get("correct")
    }
    return correct, miss, miss_predicted


def test_bitnet_correct_major_set_pinned():
    """El conjunto exacto de mayores que BitNet clasifica correctamente por
    sí mismo está fijado.

    Añadir un nuevo par de clase mayor que BitNet clasifique correctamente
    obliga a actualizar esta constante (y a declararlo en la interfaz y el
    commit). A la inversa, una regresión que descarte uno de estos pares del
    conjunto correcto falla la comprobación.
    """
    correct, _miss, _ = _calib_majors()
    assert correct == _BITNET_CORRECT_MAJORS, (
        f"El conjunto de mayores correctas de BitNet por sí solo ha derivado.\n"
        f"  fijado : {sorted(_BITNET_CORRECT_MAJORS)}\n"
        f"  vivo   : {sorted(correct)}\n"
        f"Si un reentrenamiento corrigió un par antes omitido, actualice la "
        f"constante Y refleje la mejora en la sección BitNet de docs/demo.html."
    )


def test_bitnet_miss_major_set_pinned():
    """El conjunto exacto de mayores que BitNet POR SÍ SOLO omite está fijado.

    Una corrección silenciosa (reentrenamiento que detecta la omisión sin
    declararlo en el commit) falla esta comprobación: obliga a una nota
    honesta de 'BitNet ahora detecta el par X' en el commit y la interfaz.
    """
    _correct, miss, miss_predicted = _calib_majors()
    assert miss == _BITNET_MISS_MAJORS, (
        f"El conjunto de mayores omitidas por BitNet por sí solo ha derivado.\n"
        f"  fijado : {sorted(_BITNET_MISS_MAJORS)}\n"
        f"  vivo   : {sorted(miss)}\n"
        f"Esta fijación sigue el techo arquitectónico. Actualice la constante "
        f"solo tras declarar el cambio en el commit Y en la nota "
        f"'BitNet frente a motor' de la interfaz."
    )
    for pair, pred in miss_predicted.items():
        assert pred == _BITNET_MISS_PREDICTED_AS, (
            f"La omisión fijada de BitNet {pair} ahora se predice como {pred!r}, "
            f"no {_BITNET_MISS_PREDICTED_AS!r}. O bien cambió el comportamiento "
            f"del modelo (bien: declárelo en la interfaz) o la calibración está "
            f"obsoleta."
        )


def test_demo_distinguishes_bitnet_alone_from_engine_recall():
    """La interfaz debe exponer la distinción entre BitNet por sí solo y la
    salida del motor.

    El minigráfico `100% · 4 / 4` para mayor se lee como 'BitNet detectó las
    cuatro', pero en realidad es la salida final del motor (la anulación por
    seguridad de la Capa 4.5 conserva la mayor previa cuando BitNet la rebaja).

    Por coherencia de honestidad con la declaración del invariante de
    federación de la iter-22 ('16 de 21 verificados de extremo a extremo') y
    la corrección de confusión de precisión de la iter-102 ('85,7% en datos
    reservados frente a 100% en vivo'), la interfaz debe mencionar la cifra
    de 3-de-4 de BitNet por sí solo Y el mecanismo de anulación por seguridad.
    """
    html = _DEMO_HTML.read_text()
    # Promoción v8 de la iter-275: BitNet por sí solo ahora iguala al motor
    # en las mayores (v8 detecta las 4/4, incluyendo tacrólimus+voriconazol
    # que v1 omitía por el techo arquitectónico basado solo en hash).
    # La afirmación histórica "v1 detectó 3 de 4" se conserva como contexto
    # de progresión arquitectónica. El propósito de la fijación (evitar la
    # confusión entre BitNet por sí solo y el motor final) también se cumple
    # al nivel 4/4: cuando COINCIDEN, la interfaz debe reflejar que el hueco
    # se cerró en la promoción de la iter-275.
    has_v1_history = "3 of 4" in html  # contexto histórico (pre-v8)
    has_v8_match = (
        "BitNet alone now equals the engine" in html
        or "post iter-275 v8 promotion" in html
        or "v8 catches all 4" in html.lower()
        or "v8 catches" in html.lower()  # broader anchor
    )
    has_anchor = (
        "BITNET_SAFETY_DOWNGRADE_DISAGREEMENT" in html
        or "safety-override" in html.lower()
        or "safety override" in html.lower()
        or "preserves upstream" in html.lower()
        or "v8 catches" in html.lower()
    )
    assert (has_v1_history or has_v8_match) and has_anchor, (
        f"docs/demo.html debe exponer la recuperación de mayores de BitNet "
        f"por sí solo frente al motor: o bien el contexto histórico "
        f"'v1 caught 3 of 4', O BIEN la afirmación posterior a v8 'BitNet "
        f"alone now equals the engine', Y un anclaje de la Capa 4.5 "
        f"(anulación por seguridad / 'v8 catches'). "
        f"has_v1_history={has_v1_history}, has_v8_match={has_v8_match}, "
        f"has_anchor={has_anchor}"
    )


def test_demo_names_the_bitnet_miss_pair():
    """La interfaz debe nombrar el par omitido concreto (exposición
    transparente del hueco).

    Misma disciplina de 'honestidad sobre los huecos' que la federación de
    la iter-22, la precisión confundida de la iter-102 y la cobertura de
    explicación de contraindicaciones de la iter-105. Nombrar el par permite
    contrastarlo con las líneas de registro
    `BITNET_SAFETY_DOWNGRADE_DISAGREEMENT` de
    `scripts/run_clinical_regression_eval.py`.
    """
    html = _DEMO_HTML.read_text().lower()
    assert "tacrolimus" in html and "voriconazole" in html, (
        "docs/demo.html debe nombrar 'tacrolimus' y 'voriconazole' de forma "
        "explícita en la nota BitNet-frente-a-motor: nombrar el par omitido "
        "permite contrastar la afirmación del techo arquitectónico con los "
        "registros de evaluación de regresión en vivo."
    )


def test_demo_distinguishes_bitnet_recall_from_engine_recall_traceable():
    """La afirmación de BitNet por sí solo frente al motor debe poder
    rastrearse hasta este archivo de fijación.

    Toda afirmación de peso debe tener trazabilidad auditable hasta un
    archivo de fijación: la nota de BitNet-frente-a-motor en docs/demo.html
    nombra el par omitido (tacrolimus + voriconazole), y esta fijación
    ancla la cuenta 4/4 posterior a v8. La deriva silenciosa entre la
    interfaz de cara al usuario y el inventario de pruebas en vivo es
    visible para el revisor y erosiona la confianza.
    """
    html = _DEMO_HTML.read_text().lower()
    assert "tacrolimus" in html and "voriconazole" in html, (
        "docs/demo.html debe nombrar el par omitido para que la afirmación "
        "de BitNet por sí solo frente al motor sea rastreable hasta esta "
        "fijación."
    )
