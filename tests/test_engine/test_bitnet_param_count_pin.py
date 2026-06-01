"""Fija los recuentos de parámetros de BitNet.

**Promoción v8 de la iter-275**: paquete del motor 1f0f8859 (Vía A v8,
codificador de hash de 193 dimensiones + indicador ATC + derivados de par,
hidden=256). Arquitectura:

    in_features:   193
    hidden:        256   →  hidden_w (193 × 256 = 49408) + hidden_b (256)
    out_features:    5   →  output_w (256 × 5  =  1280)  + output_b (5)
    Pesos ternarios: 49408 + 1280 = 50.688
    Sesgos Q16.16 :    256 +    5 =    261
    TOTAL params  : 50.688 + 261 = 50.949

Antes de v8 (cfadb4f6, codificador v1 basado solo en hash, hidden=64) había
8.512 pesos ternarios / 69 sesgos / 8.581 en total. El doble arquitectónico
de v8 + la extensión de bits de indicador produce ~6 veces el presupuesto de
parámetros; el barrido de la iter-244 demostró que el nuevo margen es lo que
cerró el techo de generalización de v1→v7.

Esta prueba fija esos recuentos para que un cambio futuro en la forma de los
pesos no pueda reintroducir deriva en silencio.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLE = _REPO_ROOT / "engine" / "bitnet_weights.json"

# Arquitectura v8 (promoción de la iter-275): 193 × 256 + 256 × 5 + 256 + 5
_EXPECTED_TERNARY = 49408 + 1280  # 50.688
_EXPECTED_BIASES = 256 + 5         # 261
_EXPECTED_TOTAL = _EXPECTED_TERNARY + _EXPECTED_BIASES  # 50.949


def _flat(x):
    if isinstance(x, list):
        for el in x:
            yield from _flat(el)
    else:
        yield x


def _counts() -> dict[str, int]:
    payload = json.loads(_BUNDLE.read_text())
    counts = {
        "hidden_w": sum(1 for _ in _flat(payload["hidden_w"])),
        "hidden_b": sum(1 for _ in _flat(payload["hidden_b"])),
        "output_w": sum(1 for _ in _flat(payload["output_w"])),
        "output_b": sum(1 for _ in _flat(payload["output_b"])),
    }
    counts["ternary"] = counts["hidden_w"] + counts["output_w"]
    counts["biases"] = counts["hidden_b"] + counts["output_b"]
    counts["total"] = counts["ternary"] + counts["biases"]
    return counts


def test_bundle_param_counts_match_pinned():
    c = _counts()
    assert c["ternary"] == _EXPECTED_TERNARY, (
        f"el recuento de pesos ternarios ha derivado: vivo={c['ternary']}, fijado={_EXPECTED_TERNARY}"
    )
    assert c["biases"] == _EXPECTED_BIASES, (
        f"el recuento de sesgos ha derivado: vivo={c['biases']}, fijado={_EXPECTED_BIASES}"
    )
    assert c["total"] == _EXPECTED_TOTAL, (
        f"el recuento total de parámetros ha derivado: vivo={c['total']}, fijado={_EXPECTED_TOTAL}"
    )


def test_no_stale_8517_remains_in_user_facing_docs():
    """El error tipográfico 8.517 no debe aparecer en ningún documento de cara
    al usuario ni en el panel.

    El número correcto es 8.581. Esta prueba detecta rotaciones a medio
    completar que corrigen una mención y omiten otras.
    """
    files = (
        _REPO_ROOT / "README.md",
        _REPO_ROOT / "docs" / "demo.html",
        _REPO_ROOT / "docs" / "bitnet_training.md",
    )
    for p in files:
        if not p.exists():
            continue
        text = p.read_text()
        assert "8,517" not in text and "8517" not in text, (
            f"Recuento de parámetros obsoleto '8,517' (error por 8,581) aún en {p.relative_to(_REPO_ROOT)}"
        )

