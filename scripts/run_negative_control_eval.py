"""Comprobación de precisión con controles negativos.

Complementa scripts/run_clinical_regression_eval.py (recall sobre
interacciones positivas) ejecutando la cadena contra un conjunto
seleccionado de no-interacciones clínicamente conocidas e informando la
tasa de falsos positivos.

Objetivo: cada par de docs/negative_control_cohort.json debe producir
``severity in {"none", "minor"}`` desde la cadena determinista.
Cualquier resultado más estricto es un falso positivo (FP) — un fallo de precisión.

Uso:
    python3 scripts/run_negative_control_eval.py
    python3 scripts/run_negative_control_eval.py --json   # legible por máquina

Código de salida:
    0  -- todos los pares cumplen (sin falsos positivos a partir de moderate)
    1  -- uno o más falsos positivos detectados

Apache-2.0 — STARGA, Inc.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Permite importar el paquete engine cuando se ejecuta directamente.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_clinical_regression_eval import _pipeline_severity  # noqa: E402

_COHORT = _REPO_ROOT / "docs" / "negative_control_cohort.json"
# Rango de gravedad: cualquier resultado moderate o más estricto en una no-interacción es un FP.
_FP_THRESHOLD_RANK = 3  # moderate
_RANK = {"none": 0, "minor": 1, "moderate": 3, "serious": 4, "major": 4, "contraindicated": 5}


def _is_fp(verdict: str) -> bool:
    return _RANK.get(verdict.lower(), 0) >= _FP_THRESHOLD_RANK


def _run() -> dict:
    cohort = json.loads(_COHORT.read_text())
    results = []
    for pair in cohort:
        a, b = pair["drug_a"], pair["drug_b"]
        t0 = time.perf_counter()
        verdict = _pipeline_severity(a, b)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        results.append(
            {
                "drug_a": a,
                "drug_b": b,
                "expected_severity": pair["expected_severity"],
                "pipeline_verdict": verdict,
                "is_fp": _is_fp(verdict),
                "latency_ms": round(dt_ms, 3),
            }
        )
    total = len(results)
    fp = sum(1 for r in results if r["is_fp"])
    return {
        "total_pairs": total,
        "false_positives": fp,
        "precision": (total - fp) / total if total else 1.0,
        "fpr": fp / total if total else 0.0,
        "mean_latency_ms": round(
            sum(r["latency_ms"] for r in results) / total if total else 0.0, 3
        ),
        "per_pair_results": results,
    }


def _print_human(report: dict) -> None:
    print("=" * 72)
    print("Historia Clínica — Comprobación de precisión con controles negativos")
    print("=" * 72)
    print(f"  Cohorte        : {_COHORT}")
    print(f"  Total de pares : {report['total_pairs']}")
    print(f"  Falsos positivos : {report['false_positives']}")
    print(f"  Precisión      : {report['precision']:.4f}")
    print(f"  FPR            : {report['fpr']:.4f}")
    print(f"  Latencia media : {report['mean_latency_ms']:.3f} ms")
    print("-" * 72)
    for r in report["per_pair_results"]:
        marker = "INCUMPLE" if r["is_fp"] else "OK"
        print(
            f"  [{marker}] {r['drug_a']:20} + {r['drug_b']:25} "
            f"→ {r['pipeline_verdict']:15} "
            f"(se esperaba ≤ minor)"
        )
    print("=" * 72)
    if report["false_positives"] == 0:
        print("  BARRERA DE PRECISIÓN: CUMPLE — sin falsos positivos.")
    else:
        print(f"  BARRERA DE PRECISIÓN: INCUMPLE — {report['false_positives']} FP detectados.")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emite JSON legible por máquina")
    args = ap.parse_args()
    report = _run()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 0 if report["false_positives"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
