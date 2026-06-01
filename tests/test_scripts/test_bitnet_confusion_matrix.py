"""Pruebas para `scripts/build_bitnet_confusion_matrix.py`.

El artefacto `docs/bitnet_confusion_matrix.json` es el complemento de
grado auditable a la fijación de precisión de la clase 'contraindicated'
de la iteración 29. Da a los auditores una imagen completa de un vistazo
de dónde la capa 4.5 BitNet es precisa (contraindicated: precisión 1.000)
y dónde el pipeline de 4 niveles aguas arriba lleva la carga (serious:
BitNet rara vez predice esta clase, por diseño).

Invariantes fijados:
  • FP_contraindicated == 0 (la afirmación de seguridad que justifica
    el encuadre de "veto de alta precisión" en el panel).
  • TP_contraindicated ≥ 6 (piso de sensibilidad: antes de la iteración 49
    era 6/17, el crecimiento de cohorte de la iteración 49 lo hace 6/18,
    este piso detecta una rotación de pesos que cae por debajo de 6/N).
  • La matriz en vivo coincide con el artefacto en disco (puerta de paridad de CI).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARTIFACT = _REPO_ROOT / "docs" / "bitnet_confusion_matrix.json"
_SCRIPT = _REPO_ROOT / "scripts" / "build_bitnet_confusion_matrix.py"


@pytest.fixture(scope="module")
def matrix() -> dict:
    """Carga el artefacto en disco."""
    assert _ARTIFACT.exists(), (
        f"Ejecutar scripts/build_bitnet_confusion_matrix.py para regenerar {_ARTIFACT}"
    )
    return json.loads(_ARTIFACT.read_text())


def test_artifact_has_required_keys(matrix):
    for key in (
        "matrix",
        "per_class",
        "safety_invariants",
        "weights_id",
        "cache_pairs_total",
    ):
        assert key in matrix, f"falta la clave: {key}"


def test_safety_invariant_fp_contraindicated_is_zero(matrix):
    """La afirmación de seguridad principal: BitNet nunca predice falsamente 'contraindicated'."""
    assert matrix["safety_invariants"]["fp_contraindicated_is_zero"] is True, (
        "Un falso positivo de la capa 4.5 en 'contraindicated' invalidaría el "
        "encuadre de 'veto de alta precisión'. Investigar la rotación de pesos."
    )
    fp = matrix["per_class"]["contraindicated"]["fp"]
    assert fp == 0, f"el FP de 'contraindicated' se desvió: {fp}, fijado=0"


def test_safety_invariant_tp_contraindicated_floor(matrix):
    """Piso de sensibilidad: al menos 7 TP de 'contraindicated' en la caché en vivo.

    Trinquete de la iteración 117: subió de 6 a 7 porque BitNet ha mantenido
    TP=7 desde la iteración 104 (detección de sumatriptán+fenelzina). El piso
    sigue el patrón de trinquete de las iteraciones 66/90: una vez que un
    invariante de clase de seguridad se ha mantenido durante muchas
    iteraciones, se sube el trinquete para que una regresión por debajo del
    valor sostenido falle la puerta.

    La clave antigua `tp_contraindicated_at_least_six` se elimina
    intencionadamente para forzar a quien la usa a actualizar; la prueba de
    bloqueo histórico de abajo detecta el nombre antiguo.
    """
    assert matrix["safety_invariants"]["tp_contraindicated_at_least_seven"] is True
    tp = matrix["per_class"]["contraindicated"]["tp"]
    assert tp >= 7, f"el TP de 'contraindicated' cayó por debajo del piso: {tp}, piso=7"
    # La clave antigua NO debe permanecer en el artefacto: un doble-clavado
    # silencioso dejaría pasar una regresión a TP=6.
    assert "tp_contraindicated_at_least_six" not in matrix["safety_invariants"], (
        "trinquete iteración 117: la clave heredada 'at_least_six' no debe permanecer "
        "en safety_invariants. Reejecutar scripts/build_bitnet_confusion_matrix.py "
        "para regenerar solo con 'at_least_seven'."
    )


def test_artifact_matches_live_computation():
    """El modo `--check` confirma que el artefacto está sincronizado con la salida en vivo de BitNet."""
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert cp.returncode == 0, (
        f"La matriz de confusión en disco se desvió de la versión en vivo:\n{cp.stdout}\n{cp.stderr}\n"
        "Reejecutar `python3 scripts/build_bitnet_confusion_matrix.py` para actualizar."
    )


def test_per_class_precision_recall_within_expected_bands(matrix):
    """Bandas laxas por clase: detectan regresiones sin acoplarse a una
    rotación de pesos exacta. Más estricta en 'contraindicated' (la clase de seguridad)."""
    pc = matrix["per_class"]

    # contraindicated — estricta: la precisión DEBE ser 1.000, piso de
    # sensibilidad 0.18 (la cohorte de la iteración 235 creció de 40 a 41,
    # ritonavir+ergotamina; la iteración 249 creció de 41 a 42,
    # quinidina+ritonavir; la iteración 254 creció de 42 a 43, vardenafilo+
    # nitroglicerina, extensión de ranura PDE5×nitrato; sensibilidad = 8/43 = 0.186.
    # La línea base de la iteración 164 fue 8/31 = 0.258.)
    assert pc["contraindicated"]["precision"] == 1.0
    assert pc["contraindicated"]["recall"] >= 0.18

    # major — clase de verdad-terreno pequeña (3 pares a fecha de la
    # iteración 93). La iteración 39 añadió el 1.º (tamoxifeno+paroxetina),
    # la iteración 83 el 2.º (claritromicina+digoxina), la iteración 93 el
    # 3.º (voriconazol+tacrolimus). BitNet predice "none" en
    # voriconazol+tacrolimus (subpredicción arquitectónica de 3 contenedores)
    # así que la sensibilidad es ahora 2/3 = 0.667. El piso laxo de 0.50
    # detecta una futura rotación de pesos que caiga por debajo de la media
    # cobertura aceptando a la vez la línea base de la iteración 93.
    assert pc["major"]["recall"] >= 0.50

    # moderate — banda laxa: precisión y sensibilidad ambas ≥ 0.40 (la
    # instantánea de la iteración 50 es precisión=0.688 sensibilidad=0.500;
    # la banda detecta regresiones significativas sin revolverse en cada
    # rotación de pesos).
    assert pc["moderate"]["precision"] >= 0.40
    assert pc["moderate"]["recall"] >= 0.40

    # serious — BitNet rara vez predice esta clase por diseño; sin aserción
    # por clase. El pipeline aguas arriba lleva la clasificación de 'serious'.


def test_matrix_totals_sum_to_cache_size(matrix):
    """Comprobación de coherencia: las entradas de la matriz suman el tamaño de la caché en vivo."""
    total = sum(sum(row.values()) for row in matrix["matrix"].values())
    assert total == matrix["cache_pairs_total"]
    # Y cache_pairs_total ≥ 107 (crecimiento de cohorte de la iteración 49: nunca se reduce).
    assert matrix["cache_pairs_total"] >= 107
