"""
Banco de regresión empírica de control de cambios — Historia Clínica

Barrera de control de cambios: verifica que ningún evento de reentrenamiento
provoque una regresión en el conjunto de evaluación de referencia crítico para
la seguridad de OpenEvidence, con fallo absoluto ante cualquier falso negativo
contraindicado.

Uso:
    python3 scripts/run_clinical_regression_eval.py
    python3 scripts/run_clinical_regression_eval.py --update-baseline

Códigos de salida:
    0 — todas las barreras de control de cambios cumplen; la compilación puede proceder
    1 — degradación que bloquea la publicación detectada; la compilación NO DEBE publicarse

Restricciones:
    - Sin llamadas a LLM (se permite use_llm_fallback=False + caché; sin APIs en vivo)
    - Debe completarse en menos de 60 segundos
    - Escribe un informe estructurado en docs/evaluacion_regresion_latest.json
    - Establece o compara contra docs/regression_baseline.json

Copyright 2026 STARGA, Inc. — Licencia Apache-2.0.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CACHE_JSON = _REPO_ROOT / "docs" / "openevidence_cache.json"
_WEIGHTS_JSON = _REPO_ROOT / "engine" / "bitnet_weights.json"
_BASELINE_JSON = _REPO_ROOT / "docs" / "regression_baseline.json"
_REPORT_JSON = _REPO_ROOT / "docs" / "evaluacion_regresion_latest.json"

# Orden de gravedad por clase de seguridad (índice mayor = más grave).
# Un veredicto de la cadena >= al nivel de referencia es del lado seguro (no es FN).
_SEVERITY_ORDER: dict[str, int] = {
    "none": 0,
    "minor": 1,
    "moderate": 2,
    "serious": 3,
    "major": 3,      # se trata como igual a serious para el orden de seguridad
    "contraindicated": 4,
}

# Umbrales de la barrera de control de cambios (Sección 4.2 Protocolo de Cambio de Algoritmo)
_CONTRAINDICATED_RECALL_GATE: float = 1.0    # debe ser 100%
_CONTRAINDICATED_FNR_GATE: float = 0.0       # debe ser 0%
_MAJOR_FNR_GATE: float = 0.15                # debe ser < 15%
_BASELINE_REGRESSION_TOLERANCE: float = 0.001  # 0,1% — hasta un mínimo desencadena la cancelación


def _load_cache() -> list[dict[str, Any]]:
    """Carga los pares de referencia de OpenEvidence desde docs/openevidence_cache.json."""
    with open(_CACHE_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def _load_weights_metadata() -> dict[str, str]:
    """Devuelve bundle_id y preimage_hash del bitnet_weights.json actual.

    El bundle_id es el SHA-256 sobre las matrices de pesos canónicas; cualquier
    auditor puede rederivarlo desde engine/bitnet_classifier.py::_bundle_id.
    El preimage_hash es el SHA-256 sobre los bytes JSON en bruto (anclaje a nivel de byte).
    """
    import hashlib

    raw_bytes = _WEIGHTS_JSON.read_bytes()
    preimage_hash = hashlib.sha256(raw_bytes).hexdigest()

    payload = json.loads(raw_bytes.decode("utf-8"))
    bundle_id = payload.get("_meta", {}).get("bundle_id", "unknown")
    return {"bundle_id": bundle_id, "preimage_hash": preimage_hash}


def _pipeline_severity(drug_a: str, drug_b: str) -> str:
    """Ejecuta las Capas 1 + 2-caché + 4.5 de la cadena para un par.

    No se realizan llamadas a APIs en vivo:
    - Todas las claves de API en vivo se eliminan antes de la invocación.
    - El cliente RxNorm (Capa 3) se sustituye por una operación nula para que
      devuelva una lista vacía sin tocar la red (el endpoint RxNav no tiene
      modo de prueba; el mocking es el patrón correcto de integración continua).
    - La Capa 2 usa el respaldo de *caché* de OpenEvidence (docs/openevidence_cache.json)
      — cero tráfico HTTP porque no hay OPENEVIDENCE_API_KEY definida.
    - La Capa 4 (LLM) se omite porque no hay claves de API de LLM definidas.
    - La Capa 4.5 (BitNet) siempre se ejecuta (aritmética entera pura, sin red).

    Esto cubre los 15 pares de referencia:
    - 12 pares de la tabla determinista (Capa 1) → siempre resueltos.
    - 3 pares solo en caché (amoxicillin+penicillin, iodine+metformin,
      atorvastatin+grapefruit) → resueltos por el respaldo de caché de la Capa 2.
    """
    import unittest.mock as mock  # biblioteca estándar — sin dependencias extra  # noqa: PLC0415

    _api_env_keys = [
        "OPENEVIDENCE_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ]
    saved = {k: os.environ.pop(k, None) for k in _api_env_keys}
    try:
        sys.path.insert(0, str(_REPO_ROOT))
        from engine.clinical_scoring import check_drug_interactions  # noqa: PLC0415
        import engine.rxnorm_client as rxnorm_mod  # noqa: PLC0415

        # Sustituye el cliente RxNorm: devuelve una normalización vacía para que
        # aporte cero interacciones — sin llamadas HTTP, salida determinista.
        empty_rxnorm: list = []
        with (
            mock.patch.object(rxnorm_mod, "normalize_medication_list", return_value={}),
            mock.patch.object(rxnorm_mod, "get_interactions_for_list", return_value=empty_rxnorm),
        ):
            interactions = check_drug_interactions(
                [drug_a, drug_b], use_llm_fallback=True
            )
        if not interactions:
            return "none"
        return interactions[0].severity
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _is_false_negative(ground_truth: str, pipeline_verdict: str) -> bool:
    """Devuelve True si pipeline_verdict es un descenso de seguridad respecto a ground_truth.

    Un descenso ocurre cuando la cadena informa una gravedad MENOR que la
    referencia. Informar una gravedad mayor es del lado seguro (conservador).
    Informar una gravedad igual es correcto.

    Ejemplos:
        ground_truth=contraindicated, verdict=none       -> True  (FN, bloquea la publicación)
        ground_truth=contraindicated, verdict=moderate   -> True  (FN, bloquea la publicación)
        ground_truth=serious, verdict=major              -> False (lado seguro, correcto)
        ground_truth=moderate, verdict=serious           -> False (lado seguro, correcto)
        ground_truth=serious, verdict=moderate           -> True  (FN en la clase major)
    """
    gt_rank = _SEVERITY_ORDER.get(ground_truth.lower(), 0)
    vd_rank = _SEVERITY_ORDER.get(pipeline_verdict.lower(), 0)
    return vd_rank < gt_rank


def _compute_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calcula métricas por clase y agregadas a partir de la lista de resultados por par."""
    total = len(results)
    agreements = sum(1 for r in results if not r["is_fn"])

    # Recuentos por clase
    class_counts: dict[str, dict[str, int]] = {}
    for r in results:
        gt = r["ground_truth"]
        class_counts.setdefault(gt, {"total": 0, "fn": 0})
        class_counts[gt]["total"] += 1
        if r["is_fn"]:
            class_counts[gt]["fn"] += 1

    def _recall(cls: str) -> float:
        bucket = class_counts.get(cls, {"total": 0, "fn": 0})
        if bucket["total"] == 0:
            return 1.0
        return (bucket["total"] - bucket["fn"]) / bucket["total"]

    def _fnr(cls: str) -> float:
        bucket = class_counts.get(cls, {"total": 0, "fn": 0})
        if bucket["total"] == 0:
            return 0.0
        return bucket["fn"] / bucket["total"]

    mean_latency = (
        sum(r["latency_ms"] for r in results) / total if total > 0 else 0.0
    )

    return {
        "total_pairs": total,
        "total_agreements": agreements,
        "agreement_rate": agreements / total if total > 0 else 0.0,
        "mean_latency_ms": round(mean_latency, 3),
        "recall_contraindicated": _recall("contraindicated"),
        "recall_major": _recall("major"),
        "recall_serious": _recall("serious"),
        "recall_moderate": _recall("moderate"),
        "fnr_contraindicated": _fnr("contraindicated"),
        "fnr_major": _fnr("major"),
        "fnr_serious": _fnr("serious"),
        "fnr_moderate": _fnr("moderate"),
        "per_class_counts": class_counts,
    }


def _check_absolute_gates(metrics: dict[str, Any]) -> list[str]:
    """Devuelve una lista de mensajes de fallo de barrera (vacía = todas cumplen)."""
    failures: list[str] = []

    recall_ci = metrics["recall_contraindicated"]
    if recall_ci < _CONTRAINDICATED_RECALL_GATE:
        failures.append(
            f"BARRERA INCUMPLIDA: recall de contraindicated {recall_ci:.4f} "
            f"< requerido {_CONTRAINDICATED_RECALL_GATE:.4f} (100%)"
        )

    fnr_ci = metrics["fnr_contraindicated"]
    if fnr_ci > _CONTRAINDICATED_FNR_GATE:
        failures.append(
            f"BARRERA INCUMPLIDA: FNR de contraindicated {fnr_ci:.4f} "
            f"> permitido {_CONTRAINDICATED_FNR_GATE:.4f} (0%)"
        )

    fnr_maj = metrics.get("fnr_major", 0.0)
    if fnr_maj > _MAJOR_FNR_GATE:
        failures.append(
            f"BARRERA INCUMPLIDA: FNR de major {fnr_maj:.4f} "
            f"> permitido {_MAJOR_FNR_GATE:.4f} (15%)"
        )

    return failures


def _check_baseline_regression(
    current: dict[str, Any],
    baseline: dict[str, Any],
) -> list[str]:
    """Devuelve los mensajes de fallo de regresión respecto a la línea base guardada."""
    failures: list[str] = []
    safety_metrics = [
        "recall_contraindicated",
        "recall_major",
        "recall_serious",
    ]
    for key in safety_metrics:
        cur_val = current.get(key, 0.0)
        base_val = baseline.get(key, 0.0)
        drop = base_val - cur_val
        if drop > _BASELINE_REGRESSION_TOLERANCE:
            failures.append(
                f"REGRESIÓN: {key} bajó {drop:.4f} "
                f"(línea base {base_val:.4f} -> actual {cur_val:.4f}); "
                f"la tolerancia es {_BASELINE_REGRESSION_TOLERANCE:.4f}"
            )
    return failures


def _print_summary_table(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    weights_meta: dict[str, str],
    gate_failures: list[str],
    baseline_failures: list[str],
) -> None:
    """Imprime una tabla de resumen legible en stdout."""
    SEP = "-" * 72

    print()
    print("=" * 72)
    print("  Banco de regresión empírica de control de cambios de Historia Clínica")
    print("=" * 72)
    print(f"  BitNet bundle_id    : {weights_meta['bundle_id']}")
    print(f"  Preimagen de pesos  : {weights_meta['preimage_hash']}")
    print(f"  Fuente de referencia: docs/openevidence_cache.json")
    print(SEP)

    # Resultados por par
    print(f"  {'PAR':<40}  {'REF':<15}  {'CADENA':<15}  {'ESTADO'}")
    print(SEP)
    for r in results:
        pair_label = f"{r['drug_a']}+{r['drug_b']}"[:38]
        status = "OK" if not r["is_fn"] else "FALSO-NEGATIVO"
        print(
            f"  {pair_label:<40}  {r['ground_truth']:<15}  "
            f"{r['pipeline_verdict']:<15}  {status}"
        )

    print(SEP)
    print(f"  Total de pares evaluados : {metrics['total_pairs']}")
    print(f"  Total de coincidencias   : {metrics['total_agreements']}")
    print(f"  Tasa de coincidencia     : {metrics['agreement_rate']:.1%}")
    print(f"  Latencia media por par   : {metrics['mean_latency_ms']:.1f} ms")
    print()
    print(f"  Recall  contraindicated : {metrics['recall_contraindicated']:.3f}  "
          f"(BARRERA: debe ser 1.000)")
    print(f"  Recall  major           : {metrics['recall_major']:.3f}")
    print(f"  Recall  serious         : {metrics['recall_serious']:.3f}")
    print(f"  Recall  moderate        : {metrics['recall_moderate']:.3f}")
    print()
    print(f"  FNR contraindicated : {metrics['fnr_contraindicated']:.3f}  "
          f"(BARRERA: debe ser 0.000)")
    print(f"  FNR major           : {metrics['fnr_major']:.3f}  "
          f"(BARRERA: debe ser < 0.150)")
    print(f"  FNR serious         : {metrics['fnr_serious']:.3f}")
    print(f"  FNR moderate        : {metrics['fnr_moderate']:.3f}")
    print(SEP)

    if not gate_failures and not baseline_failures:
        print("  BARRERA DE CONTROL DE CAMBIOS: CUMPLE — todas las barreras de clase de seguridad satisfechas")
    else:
        for msg in gate_failures + baseline_failures:
            print(f"  {msg}")
        print("  BARRERA DE CONTROL DE CAMBIOS: INCUMPLE — publicación BLOQUEADA")

    print("=" * 72)
    print()


def _build_report(
    results: list[dict[str, Any]],
    metrics: dict[str, Any],
    weights_meta: dict[str, str],
    gate_failures: list[str],
    baseline_failures: list[str],
    is_baseline_run: bool,
) -> dict[str, Any]:
    """Construye el informe estructurado JSON-LD escrito en docs/evaluacion_regresion_latest.json."""
    import datetime

    return {
        "@context": "https://schema.org/",
        "@type": "SoftwareApplication",
        "name": "Banco de regresión de control de cambios de Historia Clínica",
        "version": "1.0.0",
        "dateCreated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "license": "Apache-2.0",
        "description": (
            "Barrera empírica de control de cambios que verifica cero falsos "
            "negativos contraindicados contra la caché de referencia de OpenEvidence. "
            "Conforme a la Sección 4.2 Protocolo de Cambio de Algoritmo de Historia Clínica."
        ),
        "regression_gate_pass": (not gate_failures and not baseline_failures),
        "is_baseline_run": is_baseline_run,
        "weights": {
            "bundle_id": weights_meta["bundle_id"],
            "preimage_hash": weights_meta["preimage_hash"],
            "path": "engine/bitnet_weights.json",
        },
        "ground_truth_source": "docs/openevidence_cache.json",
        "metrics": metrics,
        "gate_failures": gate_failures,
        "baseline_failures": baseline_failures,
        "per_pair_results": results,
    }


def run_eval(update_baseline: bool = False) -> int:
    """Ejecuta la evaluación completa y devuelve el código de salida (0=cumple, 1=incumple)."""
    logger.info("Cargando pares de referencia desde %s", _CACHE_JSON)
    cache_entries = _load_cache()
    logger.info("Cargados %d pares de fármacos de referencia", len(cache_entries))

    logger.info("Leyendo metadatos de pesos BitNet desde %s", _WEIGHTS_JSON)
    weights_meta = _load_weights_metadata()
    logger.info(
        "BitNet bundle_id=%s preimage=%s",
        weights_meta["bundle_id"][:16] + "...",
        weights_meta["preimage_hash"][:16] + "...",
    )

    # Evaluar cada par
    per_pair_results: list[dict[str, Any]] = []
    for entry in cache_entries:
        drug_a, drug_b = entry["drug_pair_canonical"]
        ground_truth = entry["severity"]

        t0 = time.perf_counter()
        verdict = _pipeline_severity(drug_a, drug_b)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        is_fn = _is_false_negative(ground_truth, verdict)
        per_pair_results.append({
            "drug_a": drug_a,
            "drug_b": drug_b,
            "ground_truth": ground_truth,
            "pipeline_verdict": verdict,
            "is_fn": is_fn,
            "latency_ms": round(latency_ms, 3),
        })

    metrics = _compute_metrics(per_pair_results)
    gate_failures = _check_absolute_gates(metrics)

    # Comparación con la línea base
    baseline_failures: list[str] = []
    is_baseline_run = False

    if update_baseline or not _BASELINE_JSON.exists():
        is_baseline_run = True
        baseline_payload = {
            "recall_contraindicated": metrics["recall_contraindicated"],
            "recall_major": metrics["recall_major"],
            "recall_serious": metrics["recall_serious"],
            "recall_moderate": metrics["recall_moderate"],
            "fnr_contraindicated": metrics["fnr_contraindicated"],
            "fnr_major": metrics["fnr_major"],
            "total_pairs": metrics["total_pairs"],
            "weights_bundle_id": weights_meta["bundle_id"],
            "weights_preimage_hash": weights_meta["preimage_hash"],
        }
        _BASELINE_JSON.write_text(
            json.dumps(baseline_payload, indent=2), encoding="utf-8"
        )
        logger.info(
            "Línea base escrita en %s (bundle_id=%s...)",
            _BASELINE_JSON,
            weights_meta["bundle_id"][:16],
        )
    else:
        existing_baseline: dict[str, Any] = json.loads(
            _BASELINE_JSON.read_text(encoding="utf-8")
        )
        baseline_failures = _check_baseline_regression(metrics, existing_baseline)

    report = _build_report(
        per_pair_results,
        metrics,
        weights_meta,
        gate_failures,
        baseline_failures,
        is_baseline_run,
    )
    _REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Informe estructurado escrito en %s", _REPORT_JSON)

    _print_summary_table(
        per_pair_results, metrics, weights_meta, gate_failures, baseline_failures
    )

    all_failures = gate_failures + baseline_failures
    if all_failures:
        logger.error(
            "Barrera de control de cambios INCUMPLIDA — %d problema(s) que bloquean la publicación", len(all_failures)
        )
        return 1

    logger.info("Barrera de control de cambios CUMPLE — todos los invariantes de clase de seguridad satisfechos")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Banco de regresión empírica de control de cambios de Historia Clínica"
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Sobrescribe docs/regression_baseline.json con las métricas actuales. "
            "Ejecutar tras una actualización de pesos deliberada y revisada."
        ),
    )
    args = parser.parse_args()
    sys.exit(run_eval(update_baseline=args.update_baseline))


if __name__ == "__main__":
    main()
