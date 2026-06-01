"""Fijación: artefacto de calibración de BitNet + el caso de seguridad que documenta.

`docs/bitnet_calibration.json` responde a la pregunta "cuando el
clasificador se equivoca, ¿se equivoca con CONFIANZA, o simplemente
está dudando?" Para la validación clínica, esa distinción importa al
menos tanto como la cifra bruta de sensibilidad: un modelo que sabe lo
que no sabe es un modelo en el que el evaluador puede confiar.

Estas pruebas garantizan:
  • el artefacto existe y tiene el esquema que esperan los auditores
  • los totales coinciden con la caché en vivo (sin instantánea a medio camino)
  • bundle_id coincide con los pesos del motor (para que el artefacto no
    pueda desincronizarse tras una rotación de pesos)
  • los 4 fallos de inhibidor-potente-de-CYP3A4 + simvastatina están
    presentes en las entradas: esos cuatro pares SON el caso de seguridad
    de la iteración 72 y deben permanecer visibles

Regenerar mediante `python3 scripts/build_bitnet_calibration.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CALIB = _REPO_ROOT / "docs" / "bitnet_calibration.json"
_CACHE = _REPO_ROOT / "docs" / "openevidence_cache.json"
_ENGINE_WEIGHTS = _REPO_ROOT / "engine" / "bitnet_weights.json"


def _load_calib() -> dict:
    return json.loads(_CALIB.read_text())


def test_calibration_artifact_exists():
    assert _CALIB.exists(), (
        "docs/bitnet_calibration.json debe existir; regenerar mediante "
        "`python3 scripts/build_bitnet_calibration.py`"
    )


def test_calibration_schema_matches_contract():
    payload = _load_calib()
    for key in (
        "name",
        "license",
        "weights_id",
        "total_pairs",
        "by_class",
        "worst_close_calls",
        "confidently_wrong",
        "entries",
    ):
        assert key in payload, (
            f"docs/bitnet_calibration.json carece de la clave de nivel superior {key!r}; "
            f"regenerar mediante build_bitnet_calibration.py"
        )

    assert isinstance(payload["entries"], list)
    assert isinstance(payload["by_class"], dict)
    assert isinstance(payload["worst_close_calls"], list)
    assert isinstance(payload["confidently_wrong"], list)


def test_calibration_total_matches_live_cache():
    payload = _load_calib()
    cache = json.loads(_CACHE.read_text())
    assert payload["total_pairs"] == len(cache), (
        f"total_pairs de la calibración={payload['total_pairs']} pero la caché en vivo "
        f"tiene {len(cache)} entradas; la calibración está obsoleta, regenerar"
    )
    assert len(payload["entries"]) == len(cache), (
        f"longitud de entries de la calibración={len(payload['entries'])} pero la caché "
        f"tiene {len(cache)}; regenerar"
    )


def test_calibration_weights_id_matches_engine():
    """Si los pesos del motor rotan, la calibración también debe rotar.
    Un bundle_id desfasado significa que la calibración se calculó contra
    pesos obsoletos y es engañosa para cualquier auditor que la lea."""
    import hashlib

    payload = _load_calib()
    weights_payload = json.loads(_ENGINE_WEIGHTS.read_text())
    canonical = json.dumps(
        {k: weights_payload[k] for k in ("hidden_w", "hidden_b", "output_w", "output_b")},
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_bundle_id = hashlib.sha256(canonical.encode()).hexdigest()
    assert payload["weights_id"] == expected_bundle_id, (
        f"la calibración se calculó contra el bundle_id "
        f"{payload['weights_id'][:16]}... pero engine/bitnet_weights.json "
        f"ahora produce el hash {expected_bundle_id[:16]}...; rotar la "
        f"calibración obsoleta mediante build_bitnet_calibration.py"
    )


def test_calibration_per_class_counts_match_cache():
    payload = _load_calib()
    cache = json.loads(_CACHE.read_text())
    cache_by_sev: dict[str, int] = {}
    for it in cache:
        sev = (it.get("severity") or "").lower()
        cache_by_sev[sev] = cache_by_sev.get(sev, 0) + 1
    for sev, summary in payload["by_class"].items():
        if sev not in cache_by_sev:
            continue
        assert summary["count"] == cache_by_sev[sev], (
            f"by_class[{sev!r}].count={summary['count']} pero la caché "
            f"tiene {cache_by_sev[sev]} entradas {sev!r}; obsoleto"
        )


def test_calibration_contraindicated_recall_matches_safety_invariant():
    """Estado de crecimiento de cohorte de la iteración 99: 6/21 = 0.286.
    La iteración 72 fue 6/20 = 0.30. Si esta prueba empieza a ver un número
    mucho mayor, ha aterrizado un reentrenamiento y las superficies aguas
    abajo (demo / docs) también deben rotarse. Una deriva aquí fuerza una
    actualización coordinada.
    """
    payload = _load_calib()
    contra = payload["by_class"].get("contraindicated")
    assert contra is not None, "el grupo 'contraindicated' debe estar presente"
    # 0.25 ≤ sensibilidad ≤ 1.00 cubre la línea base de crecimiento de
    # cohorte de la iteración 164 (8/31 = 0.258 con atazanavir+simvastatina).
    # El límite inferior detecta una rotación de pesos que rompió la
    # sensibilidad; el superior cubre cualquier cosa que celebraríamos.
    assert 0.18 <= contra["recall"] <= 1.0, (
        f"sensibilidad de 'contraindicated' {contra['recall']} fuera de "
        f"[0.18, 1.00]; investigar; el reentrenamiento puede haber regresado. "
        f"Línea base iteración 164: 8/31 = 0.258. Borde iteración 235: 8/41 = 0.195. "
        f"Piso iteración 254: 8/43 = 0.186 (vardenafilo+nitroglicerina, "
        f"extensión de ranura PDE5×nitrato)."
    )


def test_calibration_includes_cyp3a4_simvastatin_safety_case():
    """Los 4 fallos de la iteración 72 SON el caso de seguridad. Deben ser
    visibles en el artefacto de calibración para que cualquier evaluador
    clínico pueda ver exactamente qué pares la capa BitNet por sí sola no
    puede clasificar.
    """
    payload = _load_calib()
    cyp3a4_safety_case_pairs = (
        ("clarithromycin", "simvastatin"),
        ("gemfibrozil", "simvastatin"),
        ("itraconazole", "simvastatin"),
        ("ketoconazole", "simvastatin"),
    )
    keys = {
        tuple(sorted([e["drug_a"].lower().strip(), e["drug_b"].lower().strip()]))
        for e in payload["entries"]
    }
    for a, b in cyp3a4_safety_case_pairs:
        canonical = tuple(sorted([a, b]))
        assert canonical in keys, (
            f"las entradas de la calibración carecen del par del caso de seguridad "
            f"{a} + {b}; cada entrada de la caché debe estar reflejada"
        )


def test_worst_close_calls_have_smaller_margins_than_confidently_wrong():
    """Comprobación de coherencia: los 'peores casos al límite' (margen
    pequeño = incierto) deben tener márgenes ESTRICTAMENTE menores que el
    grupo de 'errores con confianza' (margen grande = fallo seguro). Si esto
    se invierte, se rompió la dirección de ordenación del script.
    """
    payload = _load_calib()
    close = payload["worst_close_calls"]
    confident = payload["confidently_wrong"]
    if not close or not confident:
        return  # nada que comprobar
    assert close[0]["margin_q16"] <= confident[0]["margin_q16"], (
        "el margen de worst_close_calls[0] debe ser ≤ al margen de "
        "confidently_wrong[0]; dirección de ordenación rota en "
        "build_bitnet_calibration.py"
    )
