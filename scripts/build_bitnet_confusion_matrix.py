#!/usr/bin/env python3
"""Calcula la matriz de confusión completa del BitNet de la capa 4.5 sobre la caché en vivo.

Produce ``docs/bitnet_confusion_matrix.json``: un artefacto JSON que asocia
cada par (gravedad_real, gravedad_predicha) a un recuento, además de la
precisión / exhaustividad (recall) / TP / FP / FN por clase.

El artefacto es el complemento de grado auditable de ``test_bitnet_live_precision_pin.py``.
Esa prueba fija **únicamente** la clase «contraindicado». Este script ofrece a los
auditores una imagen completa de un vistazo de dónde el BitNet es preciso
(contraindicado: precisión 1,000) y dónde la canalización de 4 niveles previa
asume la carga (serio: el BitNet rara vez predice esta clase —por diseño—;
el consenso previo la captura en su lugar).

Ejecutar con::

    python3 scripts/build_bitnet_confusion_matrix.py

Use ``--check`` para verificar que el artefacto en disco coincide con el cálculo
en vivo (para la paridad de CI).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CACHE = _REPO_ROOT / "docs" / "openevidence_cache.json"
_OUT = _REPO_ROOT / "docs" / "bitnet_confusion_matrix.json"


_CLASSES: tuple[str, ...] = (
    "none",
    "minor",
    "moderate",
    "serious",
    "major",
    "contraindicated",
)


def _compute_matrix() -> dict:
    sys.path.insert(0, str(_REPO_ROOT))
    # iter-421 vía B: usar classifier_layer (el punto de entrada del motor en vivo)
    # para que la matriz refleje la cascada de producción. classifier_layer
    # carga automáticamente el paquete especialista de nivel 2 cuando está presente
    # y ejecuta el despacho A-congelado-luego-B-restringido que usa la canalización
    # de consenso. Recurre de forma transparente al modo solo-A cuando falta el paquete B.
    from engine.bitnet_classifier import classifier_layer, load_weights, load_weights_b  # noqa: PLC0415

    weights = load_weights()
    weights_b = load_weights_b()
    cache = json.loads(_CACHE.read_text())

    matrix: dict[str, dict[str, int]] = {
        gt: {pred: 0 for pred in _CLASSES} for gt in _CLASSES
    }

    for entry in cache:
        gt = entry["severity"]
        if gt not in matrix:
            # Clase real desconocida — se omite con un marcador claro.
            continue
        drug_a, drug_b = entry["drug_pair_canonical"]
        pred = classifier_layer(drug_a, drug_b).severity_name
        if pred not in matrix[gt]:
            # Clase nueva emitida por el clasificador — se amplía la matriz.
            for row in matrix.values():
                row[pred] = row.get(pred, 0)
        matrix[gt][pred] += 1

    per_class: dict[str, dict[str, float]] = {}
    for cls in _CLASSES:
        tp = matrix[cls][cls]
        fp = sum(matrix[gt][cls] for gt in _CLASSES if gt != cls)
        fn = sum(matrix[cls][p] for p in _CLASSES if p != cls)
        total = tp + fn
        if total == 0:
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / total
        per_class[cls] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "ground_truth_total": total,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
        }

    weights_id_a = weights.bundle_id
    weights_id_b = weights_b.bundle_id if weights_b is not None else None
    ensemble_active = weights_b is not None

    description = (
        "Matriz de confusión, del lado del despliegue en vivo, del conjunto de "
        "clasificadores BitNet ternarios Q16.16 sobre la caché de referencia "
        "OpenEvidence. La capa 4.5 despacha una barrera congelada de nivel 1 "
        "para contraindicado/mayor (paquete A, v8) hacia un especialista de "
        "nivel 2 para serio/moderado (paquete B, iter-421) bajo argmax "
        "restringido: A gana en contraindicado, B gana en todas las demás "
        "clases. Ambas pasadas hacia delante son ternarias Q16.16 e idénticas a nivel de bits."
    ) if ensemble_active else (
        "Matriz de confusión, del lado del despliegue en vivo, del clasificador "
        "BitNet ternario Q16.16 sobre la caché de referencia OpenEvidence. "
        "Modo de un solo paquete (solo A) — especialista de nivel 2 ausente."
    )

    return {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Matriz de confusión BitNet de la capa 4.5 de Historia Clínica",
        "version": "2.0.0" if ensemble_active else "1.0.0",
        "dateCreated": datetime.now(timezone.utc).isoformat(),
        "license": "Apache-2.0",
        "description": description,
        "weights_id": weights_id_a,
        "weights_id_a": weights_id_a,
        "weights_id_b": weights_id_b,
        "ensemble_active": ensemble_active,
        "cache_pairs_total": sum(
            sum(row.values()) for row in matrix.values()
        ),
        "matrix": matrix,
        "per_class": per_class,
        "safety_invariants": {
            "fp_contraindicated_is_zero": (
                sum(
                    matrix[gt]["contraindicated"]
                    for gt in _CLASSES
                    if gt != "contraindicated"
                )
                == 0
            ),
            "tp_contraindicated_at_least_seven": (
                matrix["contraindicated"]["contraindicated"] >= 7
            ),
        },
    }


def _diff(live: dict, on_disk: dict) -> list[str]:
    """Devuelve las diferencias no triviales entre los artefactos en vivo y en disco.

    Ignora la marca de tiempo dateCreated.
    """
    live_copy = {k: v for k, v in live.items() if k != "dateCreated"}
    on_disk_copy = {k: v for k, v in on_disk.items() if k != "dateCreated"}
    if live_copy == on_disk_copy:
        return []
    diffs: list[str] = []
    for k in set(live_copy) | set(on_disk_copy):
        if live_copy.get(k) != on_disk_copy.get(k):
            diffs.append(f"{k}: live={live_copy.get(k)!r} vs on_disk={on_disk_copy.get(k)!r}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verifica que el artefacto en disco coincide con el cálculo en vivo; sale con 1 si hay diferencias",
    )
    args = parser.parse_args()

    live = _compute_matrix()

    if args.check:
        if not _OUT.exists():
            print(f"ERROR — {_OUT} no existe")
            return 1
        on_disk = json.loads(_OUT.read_text())
        diffs = _diff(live, on_disk)
        if diffs:
            print("ERROR — el artefacto en disco difiere del cálculo en vivo:")
            for d in diffs:
                print(f"  • {d}")
            return 1
        print(f"CORRECTO — {_OUT} coincide con el cálculo en vivo")
        return 0

    _OUT.write_text(json.dumps(live, indent=2) + "\n")
    print(f"se escribió {_OUT}")

    # Imprimir un resumen legible para humanos
    print("\nValor real → Predicho")
    print(f"{'Real':>16s} | " + " ".join(f"{c[:5]:>6s}" for c in _CLASSES) + " | total")
    for gt in _CLASSES:
        total = sum(live["matrix"][gt].values())
        if total == 0:
            continue
        cells = " ".join(f"{live['matrix'][gt][p]:>6d}" for p in _CLASSES)
        print(f"{gt:>16s} | {cells} | {total:>5d}")

    print("\nPrecisión / exhaustividad por clase:")
    for cls, m in live["per_class"].items():
        print(
            f"  {cls:>16s}: precisión={m['precision']:.3f} "
            f"exhaustividad={m['recall']:.3f} (tp={m['tp']} fp={m['fp']} fn={m['fn']})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
