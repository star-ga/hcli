"""Pruebas para `scripts/build_reproducibility_manifest.py`.

El manifiesto de reproducibilidad es el **único artefacto de auditoría**
que un revisor de validación clínica incorpora a una revisión de
cumplimiento para verificar de una sola vez cada superficie determinista
de carga: caché + pesos + matriz de confusión + cohorte + plan_hashes del
flujo + veredictos de las puertas + recuento de pruebas + HEAD de git.

Estas pruebas fijan los invariantes estructurales del manifiesto:
  - Todos los SHA de artefacto requeridos están presentes y bien formados
    (hex de 64 caracteres).
  - El mapa de plan_hashes del flujo coincide exactamente con los archivos
    `flows/*.flow.mind`.
  - Los invariantes de seguridad de la matriz de confusión de BitNet se
    propagan.
  - Los 4 veredictos de las puertas son PASS.
  - test_count ≥ un piso fijo.
  - El manifiesto en disco coincide con el cálculo en vivo (comprobación de paridad).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MANIFEST = _REPO_ROOT / "docs" / "reproducibility_manifest.json"
_SCRIPT = _REPO_ROOT / "scripts" / "build_reproducibility_manifest.py"
_FLOWS_DIR = _REPO_ROOT / "flows"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def manifest() -> dict:
    assert _MANIFEST.exists(), (
        f"Ejecutar scripts/build_reproducibility_manifest.py para regenerar "
        f"{_MANIFEST}"
    )
    return json.loads(_MANIFEST.read_text())


def test_manifest_has_required_top_level_keys(manifest):
    for key in (
        "@context",
        "@type",
        "name",
        "version",
        "dateCreated",
        "license",
        "git_head",
        "artifacts",
        "gates",
        "test_count",
        "audit_replay_hint",
    ):
        assert key in manifest, f"falta la clave de nivel superior: {key}"


def test_manifest_artifact_shas_are_well_formed(manifest):
    """Cada SHA-256 de artefacto debe ser un resumen hex de 64 caracteres."""
    for name, info in manifest["artifacts"].items():
        if name == "flow_plan_hashes":
            # mapeo anidado gestionado por una prueba aparte
            continue
        assert "sha256" in info, f"artefacto {name}: falta sha256"
        assert _HEX64.match(info["sha256"]), (
            f"artefacto {name}: sha256 mal formado {info['sha256']!r}"
        )


def test_manifest_flow_plan_hashes_match_flow_sources(manifest):
    """El diccionario flow_plan_hashes del manifiesto debe coincidir
    exactamente con flows/*.flow.mind en disco (una entrada por flujo, todas
    hex de 64 caracteres)."""
    flow_files = {p.stem.replace(".flow", "") for p in _FLOWS_DIR.glob("*.flow.mind")}
    manifest_flows = set(manifest["artifacts"]["flow_plan_hashes"].keys())
    assert flow_files == manifest_flows, (
        f"deriva del conjunto de flujos: en-disco={sorted(flow_files)} "
        f"manifiesto={sorted(manifest_flows)}"
    )
    for name, h in manifest["artifacts"]["flow_plan_hashes"].items():
        assert _HEX64.match(h), f"flujo {name}: plan_hash mal formado {h!r}"


def test_manifest_safety_invariants_pass_through(manifest):
    """Los booleanos fp_contraindicated_is_zero +
    tp_contraindicated_at_least_seven de la matriz de confusión de BitNet
    deben propagarse al manifiesto. Si cualquiera cambia, todo el manifiesto
    es inválido.

    Trinquete de la iteración 117: la clave del piso subió de 'at_least_six'
    a 'at_least_seven' porque BitNet ha mantenido TP=7 desde la iteración 104.
    """
    inv = manifest["artifacts"]["bitnet_confusion_matrix"]["safety_invariants"]
    assert inv.get("fp_contraindicated_is_zero") is True, (
        "Un falso positivo de la capa 4.5 en 'contraindicated' invalida el "
        "manifiesto. Reejecutar scripts/build_bitnet_confusion_matrix.py e "
        "investigar la rotación de pesos antes de reconstruir el manifiesto."
    )
    assert inv.get("tp_contraindicated_at_least_seven") is True, (
        "El TP de 'contraindicated' de la capa 4.5 cayó por debajo del piso de 7. "
        "Reejecutar scripts/build_bitnet_confusion_matrix.py e investigar."
    )


def test_manifest_all_gates_pass(manifest):
    """La secuencia de auditoría de cinco puertas debe mostrar PASS en cada línea.

    La iteración 90 promovió el verificador de repetición de auditoría desde
    un `verify_audit_replay.py --check` independiente a run_all_gates.py, y el
    diccionario `gates` del manifiesto ahora incluye `audit_replay` junto a
    las cuatro de la iteración 20. Futuras adiciones de puertas amplían
    `expected` aquí.

    Nota: arch_mind_l1 puede reportar SKIP en entornos sin el binario
    arch-mind interno de STARGA; SKIP solo es aceptable para esa puerta.
    """
    gates = manifest["gates"]
    expected = {
        "regression_recall",
        "negative_control_precision",
        "federation_invariant",
        "arch_mind_l1",
        "audit_replay",
    }
    assert set(gates.keys()) == expected
    for name, verdict in gates.items():
        if name == "arch_mind_l1":
            assert verdict in ("PASS", "SKIP"), (
                f"La puerta {name} reportó {verdict!r}; se esperaba PASS (binario presente) "
                f"o SKIP (binario ausente)."
            )
        else:
            assert verdict == "PASS", (
                f"La puerta {name} reportó {verdict!r} cuando se construyó el manifiesto. "
                f"Reejecutar esa puerta, corregir la regresión y reconstruir."
            )


def test_manifest_test_count_at_or_above_floor(manifest):
    """El recuento de pruebas recolectadas debe mantenerse en o por encima
    del piso de la iteración 54. Si alguna vez cae por debajo, o bien se
    eliminaron pruebas o el manifiesto está obsoleto."""
    assert manifest["test_count"] >= 869, (
        f"test_count del manifiesto = {manifest['test_count']}, piso = 869"
    )


def test_manifest_git_head_present(manifest):
    """git_head puede ser un SHA de 40 caracteres o 'unavailable' (CI sin
    git); nunca vacío."""
    head = manifest.get("git_head", "")
    assert head, "git_head no debe estar vacío"
    assert head == "unavailable" or re.match(r"^[0-9a-f]{40}$", head)


def test_manifest_tracks_all_load_bearing_artifacts(manifest):
    """La iteración 75 añadió `docs/bitnet_calibration.json`: un artefacto de
    auditoría de carga (enlazado desde la documentación y la demostración,
    fijado por 8 pruebas). Debe estar en los artefactos rastreados del
    manifiesto de reproducibilidad para que el SHA quede direccionado por
    contenido; de lo contrario, un atacante que modificara el archivo pero
    dejara el manifiesto obsoleto pasaría inadvertido para la puerta de
    paridad --check. Esta prueba detecta esa deriva."""
    expected_artifacts = {
        "openevidence_cache",
        "bitnet_weights",
        "bitnet_confusion_matrix",
        "cohort_coverage_matrix",
        "synthea_demo_cohort",
        "bitnet_calibration",
        "audit_replay_pins",
        "pharmacology_flags",
        "flow_plan_hashes",
    }
    actual = set(manifest["artifacts"].keys())
    missing = expected_artifacts - actual
    assert not missing, (
        f"Al manifiesto de reproducibilidad le faltan artefactos rastreados: {sorted(missing)}. "
        f"Actualizar scripts/build_reproducibility_manifest.py para incluirlos "
        f"y luego regenerar el manifiesto."
    )


def test_manifest_calibration_weights_id_matches_engine(manifest):
    """La entrada de calibración en el manifiesto debe registrar el mismo
    weights_id (bundle_id) que el paquete de pesos del motor. Si se desvían,
    la calibración se calculó contra pesos obsoletos."""
    calib = manifest["artifacts"].get("bitnet_calibration", {})
    weights = manifest["artifacts"].get("bitnet_weights", {})
    cal_wid = calib.get("weights_id")
    eng_bid = weights.get("bundle_id")
    if cal_wid is None or eng_bid is None:
        return  # ruta de arranque inicial: nada que comprobar
    assert cal_wid == eng_bid, (
        f"weights_id de la calibración={cal_wid[:16]}... pero el "
        f"bundle_id del motor={eng_bid[:16]}...; la calibración está obsoleta, "
        f"reejecutar scripts/build_bitnet_calibration.py"
    )


def test_manifest_audit_replay_bundle_id_matches_engine(manifest):
    """La entrada audit_replay_pins debe registrar el mismo bundle_id que el
    paquete de pesos del motor. Si se desvían, las fijaciones de repetición
    de auditoría se capturaron contra pesos obsoletos y `--check` mostraría
    o bien 'bundle_id_rotated' o discrepancias de repro_hash. Misma clase de
    deriva que test_manifest_calibration_weights_id_matches_engine."""
    audit = manifest["artifacts"].get("audit_replay_pins", {})
    weights = manifest["artifacts"].get("bitnet_weights", {})
    audit_bid = audit.get("bundle_id")
    eng_bid = weights.get("bundle_id")
    if audit_bid is None or eng_bid is None:
        return  # ruta de arranque inicial
    assert audit_bid == eng_bid, (
        f"bundle_id de audit_replay_pins={audit_bid[:16]}... pero el "
        f"bundle_id del motor={eng_bid[:16]}...; las fijaciones están obsoletas, "
        f"reejecutar scripts/verify_audit_replay.py"
    )


def test_manifest_pharmacology_flags_integrity(manifest):
    """La entrada pharmacology_flags debe registrar el recuento de fármacos
    en vivo y el conjunto de claves de marca. Si se desvían, la afirmación de
    la `tabla ATC de 13 marcas` de la demostración queda hueca. Detección de
    la iteración 97: misma clase de brecha que las iteraciones 76 / 86 para
    bitnet_calibration / audit_replay_pins respectivamente.

    Pisos:
      - drug_count >= 50 (cubre la superficie de fármacos de la cohorte + caché)
      - len(flag_keys) >= 12 (el mínimo de 12 marcas que distribuimos)
      - schema_version está establecido (invariante de rastro de auditoría)
    """
    pf = manifest["artifacts"].get("pharmacology_flags", {})
    if not pf:
        return  # ruta de arranque inicial
    drug_count = pf.get("drug_count", 0)
    flag_keys = pf.get("flag_keys", [])
    schema_version = pf.get("schema_version")
    assert drug_count >= 50, (
        f"drug_count de pharmacology_flags={drug_count} < 50; la tabla "
        f"curada debe cubrir al menos la superficie de fármacos de la cohorte + caché."
    )
    assert len(flag_keys) >= 12, (
        f"pharmacology_flags tiene solo {len(flag_keys)} claves de marca; "
        f"el piso es 12 (el esquema de línea base de la iteración 96)."
    )
    assert schema_version is not None, (
        "pharmacology_flags debe llevar un campo schema_version; "
        "sin él la cadena de auditoría no puede fijar la forma del conjunto de marcas."
    )
    # Presencia de marcas críticas: las clases farmacológicas de carga
    # que la demostración afirma marcar.
    required = {
        "is_cyp3a4_strong_inhibitor",
        "is_cyp3a4_substrate",
        "is_p_gp_inhibitor",
        "is_p_gp_substrate",
        "is_statin",
        "is_anticoagulant",
        "is_maoi",
        "is_serotonergic",
    }
    missing = required - set(flag_keys)
    assert not missing, (
        f"a pharmacology_flags le faltan claves de marca requeridas: {sorted(missing)}. "
        f"Estas se referencian en el texto de la demostración y la documentación; "
        f"eliminarlas rompe el esquema publicado."
    )


def test_manifest_matches_live_computation():
    """El modo `--check` confirma que el artefacto está sincronizado con el estado en vivo.

    Tolera el intercambio PASS↔SKIP de arch_mind_l1 que ocurre cuando el
    binario arch-mind interno de STARGA está presente en algunos entornos
    (estaciones de trabajo de desarrollo) y ausente en otros (CI pública).
    Cualquier OTRA deriva sigue fallando la puerta.
    """
    cp = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True, text=True, timeout=180, cwd=str(_REPO_ROOT),
    )
    if cp.returncode != 0:
        # Deriva permitida únicamente: arch_mind_l1 PASS↔SKIP. Cualquier otra cosa falla.
        diff_lines = [
            line for line in cp.stdout.splitlines()
            if "live=" in line and "on_disk=" in line
        ]
        only_arch_mind_swap = bool(diff_lines) and all(
            "gates.arch_mind_l1" in line
            and (
                ("live='PASS'" in line and "on_disk='SKIP'" in line)
                or ("live='SKIP'" in line and "on_disk='PASS'" in line)
            )
            for line in diff_lines
        )
        if only_arch_mind_swap:
            return  # intercambio dependiente del entorno aceptado
        raise AssertionError(
            f"El manifiesto de reproducibilidad se desvió del estado en vivo.\n{cp.stdout}\n{cp.stderr}\n"
            "Reejecutar `python3 scripts/build_reproducibility_manifest.py` para actualizar."
        )
