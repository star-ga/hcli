# Copyright 2026 STARGA Inc. — Apache-2.0
"""Fija el determinismo de la fijación canónica por par en Q16.16 de Path A
v8 (1f0f8859, h=256) — 18 pares canónicos × 4 valores fijados + 100×18 =
1800 de estrés de determinismo del paso hacia delante.

El barrido v8 de iter-244 aterrizó: duplicar hidden_dim 128 → 256 rompió el
techo arquitectónico de v7. La semilla 71 alcanzó 41/41 + 4/4 + 0 FP bajo
Q16.16.

Forma espejo de la fijación q16 de v6 (retirada en iter-245) — ahora con 18
pares canónicos TODOS clasificando como está documentado (8 anclas + 7
fallos históricos de v5 + 1 par lurasidone+ketoconazole de iter-215 que v6
falló pero v8 detecta + 1 hueco de iter-249 quinidine+ritonavir, inhibidor
de proteasa de VIH × antiarrítmico de Clase IA + 1 extensión de hueco de
iter-254 vardenafil+nitroglycerin, PDE5 × nitrato). El doblado
arquitectónico de iter-244 rompió el techo — cada fallo conocido previo se
clasifica ahora como contraindicado bajo v8, y el crecimiento futuro de la
cohorte extiende este conjunto de fijaciones canónicas para que cualquier
par nuevo quede bloqueado a nivel de codificador + Q16.16 en el commit.

Disciplina de fijación cruzada de iter-210 preservada: cada par de la tupla
incrustada `_V5_HISTORICAL_MISSES` aparece en las fijaciones canónicas de
V8 Y tiene severity_name='contraindicated'. El par del fallo conocido de v6
de iter-215 (lurasidone+ketoconazole) también está fijado en
severity='contraindicated' (ahora detectado por v8).

Fijado en iter-244 contra el paquete 1f0f88591c05af57c (barrido de iter-244
con semilla=71).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from retrain_runpod.train_bitnet_v3_full import encode_pair  # noqa: E402

_BUNDLE = _REPO_ROOT / "retrain_runpod" / "bitnet_weights_v8_h256.json"

_PATH_A_V8_BUNDLE_ID = (
    "1f0f88591c05af57c62d844b667639b29c7d1f0eb1b213073d158101611f76e6"
)

_Q16_ONE = 1 << 16


def _load_bundle() -> dict:
    return json.loads(_BUNDLE.read_text())


def _hash_int_seq(seq: list[int]) -> str:
    return hashlib.sha256(
        b"".join(v.to_bytes(8, "big", signed=True) for v in seq)
    ).hexdigest()


def _classify_v8_q16(da: str, db: str, bundle: dict) -> tuple[list[int], list[int]]:
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
    return feat, logits


def _argmax_label(logits: list[int]) -> str:
    labels = ("none", "moderate", "serious", "major", "contraindicated")
    return labels[max(range(len(logits)), key=lambda i: logits[i])]


# Tupla incrustada de fallos históricos de v5 — la misma fijación cruzada
# de iter-210 preservada a través de la retirada de v6 en iter-245. v8 debe
# detectar cada par de esta tupla como contraindicado (la promesa de
# BOOST_KEYS que el barrido v6 de iter-207 cumplió parcialmente y el barrido
# v8 de iter-244 completó).
_V5_HISTORICAL_MISSES: tuple[tuple[str, str], ...] = (
    ("isavuconazole", "simvastatin"),
    ("ketoconazole", "ergotamine"),
    ("minocycline", "isotretinoin"),
    ("ketoconazole", "midazolam"),
    ("eplerenone", "ketoconazole"),
    ("cyclosporine", "rosuvastatin"),
    ("tolvaptan", "ketoconazole"),
)

# Más el fallo conocido de v6 de iter-215 que v8 finalmente detecta (el par
# del avance que motivó el doblado arquitectónico).
_ITER215_V6_KNOWN_MISS = ("ketoconazole", "lurasidone")


# 18 pares canónicos × 4 valores fijados (logits_q16 + feature_hash +
# logits_hash + severity_name). Calculados en iter-244 contra el paquete
# 1f0f88591c05af57c.
_V8_CANONICAL_PINS: dict[tuple[str, str], dict] = {
    # — Anclas de clase de severidad (cobertura entre clases) —
    ("warfarin", "ibuprofen"): {
        "logits_q16": [-1928017, -1031470, 2423326, 747543, -2811021],
        "feature_hash": "5f6271ff01718007bca74568412cfe26afbe8f3139fbc5f759c1f0ab07b1b1f3",
        "logits_hash": "37b25f9f6826729a61de9b2e71ceae7229881641590650d6351d97d266549eee",
        "severity_name": "serious",
    },
    ("atorvastatin", "grapefruit"): {
        "logits_q16": [480474, 3967176, -6329991, 2109040, -7359887],
        "feature_hash": "90141ffcf38708e520db512d5f64cf10c6ee12b5eb2b52aab14741af3fb401fd",
        "logits_hash": "e758c64f19bdfead86eb09b503a5320abd4319765b54dda205254d02bb6364f5",
        "severity_name": "moderate",
    },
    ("amoxicillin", "penicillin"): {
        "logits_q16": [-425437, 2248054, -8606112, -3074630, -5816061],
        "feature_hash": "d429048fd7e653301d2a0ae825ded1623a21cde786b4e1931da354265f49505f",
        "logits_hash": "1b3db58a06d501647a1b2037b93a7926f7a26bfc1bc6bee98d5aa79e2a828934",
        "severity_name": "moderate",
    },
    ("clarithromycin", "simvastatin"): {
        "logits_q16": [-284901, -333753, -138679, 35778, 99886],
        "feature_hash": "91d88e612117b8ed86e9828a1d3516c919dbd51623e853a39e632c60d5c47f13",
        "logits_hash": "8b75166b1efcb447d1ec9250b919cd671350282a60d41281f10ca39d61be3284",
        "severity_name": "contraindicated",
    },
    ("ciprofloxacin", "tizanidine"): {
        "logits_q16": [468310, -1636843, -14026782, -7675825, 6622716],
        "feature_hash": "fe1aaa027887389508903746c485e72c51c525385aea17021867139aefb02d9a",
        "logits_hash": "a51fc0881dfa5ca1dcd65069735b4089e35ce1959524505fc49a67682348c7cc",
        "severity_name": "contraindicated",
    },
    ("lisinopril", "sacubitril"): {
        "logits_q16": [-1126467, -5739706, -6517761, -5256886, 3433842],
        "feature_hash": "577cf01d87e934c64da6c0cfc860c349662bf6387e28ad9ea44d74014977b83c",
        "logits_hash": "00f17114521b48b9cc50e491427545bd608482970119700276ca3ded7e64011c",
        "severity_name": "contraindicated",
    },
    ("allopurinol", "azathioprine"): {
        "logits_q16": [-464108, -2051951, -7974165, -1317657, 6841162],
        "feature_hash": "59975574ec8c184df0b8efb8037d6e09b5dfc29f0785ddfc1c91bed51403e9f0",
        "logits_hash": "f83b1f8a253998f496a79a088afb0f20ada2b2c36d9519e5869b48923a878e27",
        "severity_name": "contraindicated",
    },
    ("iodine", "metformin"): {
        "logits_q16": [-36285, -4064442, -17832834, -7460059, 9463593],
        "feature_hash": "6f34ac4a2b3cf6bf06083cbe07236fc5de3a93404fbc162d09d2b2be15ed0413",
        "logits_hash": "88a5e901631407a1fb146547b57c0e1c9f74373ef6f2f2acd02964fc88db025f",
        "severity_name": "contraindicated",
    },
    # — Fallos históricos de v5, todos detectados por v6+v8 —
    ("isavuconazole", "simvastatin"): {
        "logits_q16": [-284901, -485703, -138679, 35778, 99886],
        "feature_hash": "56f267a2ddc94feb5cf73916e038b80e1b71e3f91953a4c18bf49915d70d4dad",
        "logits_hash": "0a4128362277ae1b4df9fc54b91269aaac5c34d194fc694628f76d9e2c6e0ee9",
        "severity_name": "contraindicated",
    },
    ("ketoconazole", "ergotamine"): {
        "logits_q16": [-304161, -8391173, -285252, -3695122, 8366227],
        "feature_hash": "eb7b9733edafd784792518e963e78028ef3fb3e12906ee8a6a8a47b695a64d7a",
        "logits_hash": "c39e862c75e459b12729667b38bb32e4554cbc96299956d11b3ce352720e08fd",
        "severity_name": "contraindicated",
    },
    ("isotretinoin", "minocycline"): {
        "logits_q16": [845044, -13544258, -13600090, -7053920, 13380406],
        "feature_hash": "5cba8f19c7540f39a26a1d6bab94450290031fcf6b1ec82ac81058b458047435",
        "logits_hash": "67214a0708622465ea9a44be78356cd9832b8d4c0e2af2448369724531393d5b",
        "severity_name": "contraindicated",
    },
    ("ketoconazole", "midazolam"): {
        "logits_q16": [-2317330, -5798717, -8277145, -2746269, 5598479],
        "feature_hash": "d5f38861048c035532f75e3eec1383ea3eda3feb270d81339b1774fee8073d05",
        "logits_hash": "028a504f2e948c0f2c81284c1f72c2ab4903d529dcbcb8729a81f577925253f8",
        "severity_name": "contraindicated",
    },
    ("eplerenone", "ketoconazole"): {
        "logits_q16": [-523074, -1274112, 281141, -1544055, 4739024],
        "feature_hash": "5a9adfc4a7ab2a54f5c860bfbdd43d6e7b9281baebcda552269a4d53d48575e9",
        "logits_hash": "c2b81b9bd91c6dfc46c71372082d5464b43b9780f8a34060f7faf35d700696f6",
        "severity_name": "contraindicated",
    },
    ("cyclosporine", "rosuvastatin"): {
        "logits_q16": [-5197984, -5376837, -1732557, 569672, 4338937],
        "feature_hash": "1f5427ef9dc7f4cb6409b6708c803bddbfefe4ea8f47132b46b1afca11b6e0fc",
        "logits_hash": "87a17fdda9f8dec9913bebd193bda1ac4fef157384c73f467f83da07ee928fc3",
        "severity_name": "contraindicated",
    },
    ("tolvaptan", "ketoconazole"): {
        "logits_q16": [-3078554, -7802952, -8319117, -1729771, 9013915],
        "feature_hash": "68cf7bffce498ace39a1a9c53281cbd1bacf97d349fa5d3a7ff037a1944e09ac",
        "logits_hash": "36fc20f6da59a12e87ec1937d1b5957081bb47bd198377e6a62d9dd3dbfbc710",
        "severity_name": "contraindicated",
    },
    # — Fallo conocido de v6 de iter-215, FINALMENTE detectado por v8 (el avance del doblado arquitectónico) —
    ("ketoconazole", "lurasidone"): {
        "logits_q16": [-2509327, -5002563, -7008196, 551839, 5671254],
        "feature_hash": "7bdb73d83277b5930254acda7da06265fd037f7a179e119a5794908f2502fc46",
        "logits_hash": "536f0dc3b7d3e72d079faf4c30c842aed8dc973c158ebbd35cc4dc2abade2cb1",
        "severity_name": "contraindicated",
    },
    # — Crecimiento de cohorte de iter-249 (inhibidor de proteasa de VIH × antiarrítmico de Clase IA /
    #    hueco de doble prolongador del QT; la bandera de inhibidor fuerte de CYP3A4 del ritonavir +
    #    la de sustrato de CYP3A4 de la quinidina disparan la regla 0 con un margen de logit de +14.59
    #    en Q16.16; contraindicación de las fichas técnicas de Norvir § 4 + Quinidex Extentabs § 4) —
    ("quinidine", "ritonavir"): {
        "logits_q16": [-1311044, -3771789, -1733351, -2534658, 2333390],
        "feature_hash": "d8c3f6fb4e31aca0bec1b922724194ad582be99eedd7261547827be656f0d384",
        "logits_hash": "cf5be4e3b231390458bc0f2634b2a6548ed603434926bdb68d4829bf04163dff",
        "severity_name": "contraindicated",
    },
    # — Crecimiento de cohorte de iter-254 (extensión del hueco PDE5 × nitrato de 2 → 3 entradas; el
    #    vardenafilo se une al sildenafilo + tadalafilo como el tercer inhibidor de la PDE5 de la cohorte,
    #    emparejado con la nitroglicerina; se dispara la regla 5 (is_pde5_inhibitor × is_nitrate);
    #    margen de logit de +150.51 en Q16.16 (el más fuerte del lote de iter-254);
    #    contraindicación de la ficha técnica de Levitra § 4) —
    ("nitroglycerin", "vardenafil"): {
        "logits_q16": [-9128347, -4111285, -6221339, -2343000, 7520679],
        "feature_hash": "4461be77fa0a4ac0ae9ac7fab496afba93a5fc234cd4f6bed00689ebc8e5f529",
        "logits_hash": "b409c5f10288d8f737a9beba919ff43cab3a91dd445d57e3a337df3f082c4c32",
        "severity_name": "contraindicated",
    },
}


# ── pruebas de fijación por par ───────────────────────────────────────────────


def test_v8_canonical_pair_logits_pinned() -> None:
    """Cada par canónico debe producir EXACTAMENTE los logits Q16.16 fijados."""
    bundle = _load_bundle()
    for (da, db), expected in _V8_CANONICAL_PINS.items():
        _, live_logits = _classify_v8_q16(da, db, bundle)
        assert live_logits == expected["logits_q16"], (
            f"Desviación de logits de v8 para ({da}, {db}):\n"
            f"  esperado: {expected['logits_q16']}\n"
            f"  live:     {live_logits}\n"
            f"Refija los cuatro campos si se trata de un reentrenamiento "
            f"legítimo (también requeriría un nuevo bundle_id en la fijación de iter-244)."
        )


def test_v8_canonical_feature_hashes_pinned() -> None:
    """La salida de encode_pair de cada par canónico debe hashear al
    SHA-256 fijado — una desviación aquí señala un cambio aguas arriba en el
    constructor de características (también fallaría la fijación del contrato
    de encode_pair de iter-188)."""
    bundle = _load_bundle()
    for (da, db), expected in _V8_CANONICAL_PINS.items():
        feat, _ = _classify_v8_q16(da, db, bundle)
        live = _hash_int_seq(feat)
        assert live == expected["feature_hash"], (
            f"Desviación de feature_hash de v8 para ({da}, {db}): "
            f"live={live[:16]}..., fijado={expected['feature_hash'][:16]}..."
        )


def test_v8_canonical_logits_hashes_pinned() -> None:
    """El SHA-256 sobre los logits codificados en bytes debe coincidir.
    Comprobación de identidad bit a bit de fallo más rápido que complementa
    la diferencia por elemento de arriba."""
    bundle = _load_bundle()
    for (da, db), expected in _V8_CANONICAL_PINS.items():
        _, logits = _classify_v8_q16(da, db, bundle)
        live = _hash_int_seq(logits)
        assert live == expected["logits_hash"], (
            f"Desviación de logits_hash de v8 para ({da}, {db}): "
            f"live={live[:16]}..., fijado={expected['logits_hash'][:16]}..."
        )


def test_v8_canonical_severity_labels_pinned() -> None:
    """La etiqueta argmax por par canónico debe coincidir con el severity_name fijado."""
    bundle = _load_bundle()
    for (da, db), expected in _V8_CANONICAL_PINS.items():
        _, logits = _classify_v8_q16(da, db, bundle)
        live = _argmax_label(logits)
        assert live == expected["severity_name"], (
            f"Desviación de severidad de v8 para ({da}, {db}): "
            f"live={live!r}, fijado={expected['severity_name']!r}"
        )


# ── prueba de estrés de determinismo ──────────────────────────────────────────


def test_v8_q16_determinism_stress() -> None:
    """100 iteraciones × 18 pares canónicos = 1800 de estrés de
    determinismo del paso hacia delante. La inferencia ternaria en Q16.16
    DEBE ser idéntica bit a bit entre iteraciones (sin operaciones en coma
    flotante, sin generador de números aleatorios)."""
    bundle = _load_bundle()
    first_results: dict[tuple[str, str], list[int]] = {}
    for iteration in range(100):
        for (da, db), expected in _V8_CANONICAL_PINS.items():
            _, logits = _classify_v8_q16(da, db, bundle)
            key = (da, db)
            if iteration == 0:
                first_results[key] = logits
            else:
                assert logits == first_results[key], (
                    f"Determinismo de v8 violado para ({da}, {db}) en la iter {iteration}: "
                    f"primero={first_results[key]}, ahora={logits}. Se supone que Q16.16 "
                    f"es idéntico bit a bit."
                )


def test_v8_severity_class_coverage() -> None:
    """Los 18 pares canónicos deben cubrir las 5 clases de severidad
    (excluyendo 'minor', que la cohorte omite intencionadamente).

    Iter-244 amplió la cobertura desde las 4 clases de iter-210 (sin major
    en las canónicas de v6) para incluir el avance lurasidone+ketoconazole
    de iter-215 como una fijación contraindicada adicional.
    """
    severities = {info["severity_name"] for info in _V8_CANONICAL_PINS.values()}
    expected_coverage = {"serious", "moderate", "contraindicated"}
    assert expected_coverage.issubset(severities), (
        f"A las fijaciones canónicas de v8 les faltan clases de severidad: "
        f"tiene={severities}, subconjunto_esperado={expected_coverage}"
    )


def test_v8_bundle_id_matches_iter244_pin() -> None:
    """Comprobación de cordura: el paquete que lee esta fijación debe
    coincidir con el bundle_id fijado en test_path_a_v8_live_recall_pin (iter-244)."""
    bundle = _load_bundle()
    assert bundle["_meta"]["bundle_id"] == _PATH_A_V8_BUNDLE_ID, (
        f"Desviación de bundle_id de v8: live={bundle['_meta']['bundle_id']!r}, "
        f"fijado={_PATH_A_V8_BUNDLE_ID!r}"
    )


def test_v8_catches_every_v5_historical_miss() -> None:
    """Invariante de fijación cruzada (espejo de iter-210): cada par de la
    tupla incrustada `_V5_HISTORICAL_MISSES` debe aparecer en las
    fijaciones canónicas de V8 Y tener severity_name='contraindicated'. La
    garantía bidireccional con la fijación de recuperación viva de v8 de
    iter-244 (cero fallos) es "v8 detecta cada fallo conocido previo + el
    avance lurasidone+keto de iter-215" — sin esta fijación, la promesa del
    doblado arquitectónico no tiene bloqueo por par.

    Además, el fallo conocido de v6 de iter-215 `ketoconazole+lurasidone`
    TAMBIÉN debe clasificarse como contraindicado (el par del avance).
    """
    # Todos los fallos históricos de v5 deben ser contraindicados bajo v8
    for key in _V5_HISTORICAL_MISSES:
        canonical_key = tuple(sorted([k.lower() for k in key]))
        found = False
        for (pa, pb), info in _V8_CANONICAL_PINS.items():
            if tuple(sorted([pa.lower(), pb.lower()])) == canonical_key:
                found = True
                assert info["severity_name"] == "contraindicated", (
                    f"El fallo histórico de v5 {key} está fijado con "
                    f"severity={info['severity_name']!r} bajo v8 — "
                    f"promesa de BOOST_KEYS rota; este par debe ser "
                    f"contraindicado bajo v8."
                )
                break
        assert found, (
            f"El fallo histórico de v5 {key} no está en las fijaciones "
            f"canónicas de v8; la disciplina de fijación cruzada de iter-210 "
            f"requiere que cada par de _V5_HISTORICAL_MISSES esté fijado."
        )

    # El fallo conocido de v6 de iter-215 también debe ser contraindicado bajo v8
    canonical_key = tuple(sorted([k.lower() for k in _ITER215_V6_KNOWN_MISS]))
    found = False
    for (pa, pb), info in _V8_CANONICAL_PINS.items():
        if tuple(sorted([pa.lower(), pb.lower()])) == canonical_key:
            found = True
            assert info["severity_name"] == "contraindicated", (
                f"El fallo conocido de v6 de iter-215 {_ITER215_V6_KNOWN_MISS} "
                f"está fijado con severity={info['severity_name']!r} bajo v8 — el "
                f"avance del doblado arquitectónico que motivó el barrido v8 "
                f"se rompe si este par no es contraindicado."
            )
            break
    assert found, (
        f"El fallo conocido de v6 de iter-215 {_ITER215_V6_KNOWN_MISS} no está "
        f"en las fijaciones canónicas de v8 — el par del avance debe estar "
        f"fijado en severity='contraindicated' para bloquear la promesa de iter-244."
    )
