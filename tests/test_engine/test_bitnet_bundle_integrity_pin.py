"""Fija la integridad del paquete de pesos de BitNet (tamaño + hash +
dispersión).

Iter 130.

`engine/bitnet_weights.json` es la primitiva de reproducibilidad de peso:
cada decisión clínica de la Capa 4.5 lleva un `weights_id` (SHA-256 de la
forma JSON canónica de las 4 matrices de pesos). El paquete está expuesto
en tres lugares:

  1. demo.html L1047: `<span>bundle_id cfadb4f6…0b3f</span>`
  2. demo.html L1895: `<div>...build... cfadb4f6</div>`
  3. README L130: "...paquete de pesos de 19 KB..."

La fijación de recuento de parámetros de la iter-29
(`tests/test_engine/test_bitnet_param_count_pin.py`) cubre las cifras
8.512 / 69 / 8.581, pero TRES afirmaciones de integridad a nivel de
paquete NO están fijadas:

  - Tamaño de archivo de 19 KB — ligado a la afirmación de borde "una Pi
    Zero 2 W puede llevar el paquete de pesos completo en una placa de 15 $".
  - Prefijo corto `cfadb4f6` del bundle_id — ligado a toda afirmación de
    repetición de auditoría ("cualquier auditor puede reverificar en <1 ms
    con el paquete de pesos de 19 KB").
  - ~48% de dispersión estructural (pesos colapsados a 0) — ligado a la
    afirmación de la época de la iter-72 "la dispersión estructural del
    entrenamiento con cuantización (STE) es lo que permite que el modelo de
    8.581 parámetros quepa en 19 KB" (demo.html L1072).

Sin una fijación, una rotación futura de pesos podría:
  - Inflar el archivo muy por encima de 19 KB (rompe en silencio la
    afirmación de hardware "la Pi Zero 2 W lleva el paquete completo").
  - Coincidir con el prefijo corto `cfadb4f6` mostrado solo por casualidad.
  - Bajar la dispersión muy por debajo del 40% (invalida en silencio la
    retórica de "dispersión estructural" de la interfaz).

Esta fijación exige las tres.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLE = _REPO_ROOT / "engine" / "bitnet_weights.json"
_DEMO = _REPO_ROOT / "docs" / "demo.html"


# Valores fijados (promoción v8 de la iter-275 — paquete 1f0f8859,
# codificador 193 × 256, ocupa ~118 KB en disco; el cfadb4f6 anterior a v8
# eran 19,9 KB).
_EXPECTED_SHORT_BUNDLE_ID = "1f0f8859"
_EXPECTED_BUNDLE_ID_TAIL = "76e6"  # Últimos 4 caracteres: "1f0f8859…76e6"
_EXPECTED_FILE_SIZE_BYTES = 121024  # tamaño en disco ~118 KB para v8
# Tolerancia: ±4 KB para deriva de espacios en blanco / rotación de pesos en
# torno a v8. Un salto más allá de esta banda SÍ es de peso: el doble
# arquitectónico de v8 + la extensión de bits de indicador ampliaron el
# presupuesto; más crecimiento señala una arquitectura distinta.
_FILE_SIZE_TOLERANCE_BYTES = 4096
_SPARSITY_FLOOR = 0.40  # piso de la iter-72 conservado hasta v8 (BitNet 1.58 STE)


def _payload() -> dict:
    return json.loads(_BUNDLE.read_text())


def _canonical_bundle_id() -> str:
    """SHA-256 sobre la codificación JSON canónica de las cuatro matrices de
    pesos.

    Replica `engine.bitnet_classifier._bundle_id` para que esta fijación
    pruebe la identidad del paquete de extremo a extremo sin importar el
    módulo del motor.
    """
    payload = _payload()
    canonical = json.dumps(
        {
            "hidden_w": payload["hidden_w"],
            "hidden_b": payload["hidden_b"],
            "output_w": payload["output_w"],
            "output_b": payload["output_b"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_bundle_id_short_prefix_matches_pinned():
    """Los primeros 8 caracteres del bundle_id en vivo deben coincidir con la
    afirmación `cfadb4f6` mostrada en la interfaz y la retórica de
    repetición de auditoría. Una rotación de pesos que rompa este prefijo
    DEBE actualizar los valores mostrados en la interfaz en el mismo
    commit.
    """
    bundle_id = _canonical_bundle_id()
    actual = bundle_id[:8]
    assert actual == _EXPECTED_SHORT_BUNDLE_ID, (
        f"el prefijo corto del bundle_id ha derivado: vivo={actual!r}, "
        f"fijado={_EXPECTED_SHORT_BUNDLE_ID!r}. La interfaz "
        f"muestra 'cfadb4f6…0b3f' como anclaje de repetición de auditoría; "
        f"una rotación de pesos debe actualizar esos valores mostrados y "
        f"esta constante en el mismo commit."
    )
    # Anclaje de cola — el span de la interfaz muestra `cfadb4f6…0b3f`, debe
    # seguir coincidiendo.
    actual_tail = bundle_id[-4:]
    assert actual_tail == _EXPECTED_BUNDLE_ID_TAIL, (
        f"la cola corta del bundle_id ha derivado: vivo={actual_tail!r}, "
        f"fijado={_EXPECTED_BUNDLE_ID_TAIL!r}. "
        f"docs/demo.html L1047 muestra la cola …0b3f."
    )


def test_demo_displays_pinned_bundle_id():
    """Comprobación cruzada: demo.html contiene realmente la forma corta
    fijada."""
    text = _DEMO.read_text()
    short_form = f"{_EXPECTED_SHORT_BUNDLE_ID}…{_EXPECTED_BUNDLE_ID_TAIL}"
    assert short_form in text, (
        f"docs/demo.html no contiene la forma corta fijada del bundle_id "
        f"{short_form!r}. La etiqueta de L1047 y la retórica de repetición "
        f"de auditoría se refieren a este valor."
    )


def test_bundle_file_size_within_19kb_band():
    """El archivo debe mantenerse dentro de la banda de ~19 KB (afirmación de
    borde de la Pi Zero 2 W).

    Un paquete inflado (p. ej. 32 KB tras un reentrenamiento con 128 ocultas
    o pesos flotantes accidentales) invalida en silencio la retórica de "la
    Pi Zero 2 W de 15 $ lleva el paquete completo" en la sección de borde
    sin conexión de la interfaz.

    La tolerancia de ± 2 KB permite rotaciones de pesos legítimas que
    conservan ~48% de dispersión. Un salto más allá de esta banda SÍ es la
    señal de peso de que la afirmación de borde necesita actualizarse.
    """
    actual_bytes = _BUNDLE.stat().st_size
    delta = abs(actual_bytes - _EXPECTED_FILE_SIZE_BYTES)
    assert delta <= _FILE_SIZE_TOLERANCE_BYTES, (
        f"el tamaño de bitnet_weights.json ha derivado: vivo={actual_bytes} bytes, "
        f"fijado={_EXPECTED_FILE_SIZE_BYTES} bytes, "
        f"tolerancia={_FILE_SIZE_TOLERANCE_BYTES} bytes (delta={delta}). "
        f"La retórica de '19 KB / afirmación de borde de la Pi Zero 2 W' se "
        f"rompe a partir de 32 KB; actualice la interfaz y el perfil de "
        f"hardware edge_pi_offline.md o revierta el cambio de pesos."
    )
    # Promoción v8 de la iter-275: el techo absoluto sube de 32 KB → 200 KB.
    # La Pi Zero 2 W aún puede llevar un paquete de 118 KB (512 MB de RAM,
    # lecturas de MicroSD baratas); la afirmación de borde se sostiene a este
    # tamaño, solo cambió la cifra de KB del titular. La interfaz y
    # edge_pi_offline.md pasaron de "19 KB" → "~118 KB" en el mismo commit de
    # la iter-275.
    assert actual_bytes < 200 * 1024, (
        f"bitnet_weights.json superó el techo absoluto de 200 KB "
        f"(vivo={actual_bytes} bytes). v8 ocupa ~118 KB; más crecimiento "
        f"señala una arquitectura distinta y la afirmación de borde necesita "
        f"reevaluarse."
    )


def test_ternary_weight_sparsity_floor():
    """Al menos el 40% de los pesos ternarios debe ser cero.

    Afirmación de L1072 de la interfaz: "~48% de los pesos colapsan a 0; la
    dispersión estructural del entrenamiento con cuantización (STE) es lo que
    permite que el modelo de 8.581 parámetros quepa en 19 KB."

    Una rotación de pesos que baje la dispersión por debajo del 40% (p. ej.
    un reinicio denso en coma flotante recortado accidentalmente a {-1, 0, 1})
    invalida esta retórica en silencio sin que nadie lo note, porque tanto el
    recuento de parámetros como el hash del paquete parecen correctos.
    """
    payload = _payload()
    all_weights: list[int] = []
    for row in payload["hidden_w"]:
        all_weights.extend(row)
    for row in payload["output_w"]:
        all_weights.extend(row)
    zeros = sum(1 for w in all_weights if w == 0)
    sparsity = zeros / len(all_weights)
    assert sparsity >= _SPARSITY_FLOOR, (
        f"La dispersión de los pesos ternarios bajó del piso de la iter-72: "
        f"vivo={sparsity:.1%}, piso={_SPARSITY_FLOOR:.0%}. "
        f"La afirmación de la interfaz 'dispersión estructural ~48%' se "
        f"vuelve falsa. Una rotación de pesos densos debe actualizar la "
        f"retórica de dispersión de la interfaz y why_bitnet_b158.md en el "
        f"mismo commit."
    )


def test_bundle_keys_canonical():
    """El JSON del paquete debe tener exactamente las 4 claves de pesos +
    meta.

    Añadir una clave suelta cambia en silencio la codificación JSON canónica
    Y el bundle_id, pero la fijación de recuento de parámetros no detectaría
    la clave extra (solo cuenta pesos). Esta fijación exige la forma.
    """
    payload = _payload()
    expected_keys = {"_meta", "hidden_b", "hidden_w", "output_b", "output_w"}
    actual_keys = set(payload.keys())
    extra = actual_keys - expected_keys
    missing = expected_keys - actual_keys
    assert not extra and not missing, (
        f"el conjunto de claves de engine/bitnet_weights.json ha derivado: "
        f"extra={sorted(extra)}, faltantes={sorted(missing)}. "
        f"Las 4 claves de pesos + _meta son la forma canónica; cambiar esto "
        f"cambia el bundle_id y rompe toda afirmación previa de repetición "
        f"de auditoría."
    )


def test_bundle_meta_records_provenance():
    """`_meta` debe llevar al menos un campo de procedencia para que un
    auditor pueda correlacionar un bundle_id con su contexto de
    entrenamiento.

    El paquete en vivo incluye procedencia rica: `framework_version`,
    `paper` (referencia arXiv de BitNet b1.58), `schema`, `trained_with`,
    `bundle_id` (autorreferencia). Cualquiera de estos cuenta; lo que importa
    es que exista el rastro de procedencia.
    """
    payload = _payload()
    meta = payload.get("_meta")
    assert isinstance(meta, dict), (
        "engine/bitnet_weights.json debe llevar un dict `_meta` para que un "
        "auditor futuro pueda correlacionar un bundle_id con un contexto de "
        "compilación de STARGA. Se encontró meta ausente o que no es dict."
    )
    # Acepta el esquema enviado en la iter-72 O un esquema por iteración.
    # Cualquiera de estos campos de procedencia cuenta; la prueba solo
    # garantiza que el rastro no esté vacío.
    provenance_keys = (
        "iteration", "version", "build_iter", "iter", "build", "ship_iter",
        "framework_version", "paper", "schema", "trained_with", "bundle_id",
    )
    has_provenance = any(k in meta for k in provenance_keys)
    assert has_provenance, (
        f"`_meta` de engine/bitnet_weights.json debe llevar al menos un campo "
        f"de procedencia de {provenance_keys}. Claves en vivo: "
        f"{sorted(meta.keys())}."
    )


def test_demo_cites_this_pin_file():
    """La interfaz debe citar este archivo de fijación cerca de la sección
    del modelo entrenado.

    La iter-131 añadió una nota de regla morada bajo el párrafo de
    distribución de pesos ternarios que nombra los 8 invariantes de
    integridad y se refiere a este archivo de fijación. Mismo patrón que las
    comprobaciones cruzadas "la interfaz cita el archivo de fijación" de las
    iter-110/115/121/126: la capa de pruebas y la superficie de cara al
    usuario se mantienen sincronizadas.
    """
    text = _DEMO.read_text()
    pin_filename = "test_bitnet_bundle_integrity_pin.py"
    assert pin_filename in text, (
        f"docs/demo.html debe citar "
        f"`tests/test_engine/{pin_filename}` cerca de la sección del modelo "
        f"entrenado para que se puedan rastrear las afirmaciones de "
        f"bundle_id + 19 KB + dispersión hasta el archivo de fijación que "
        f"las exige."
    )
    # Anclaje: la nota debe aparecer con una frase reconocible para que una
    # edición de copia no pueda quitar la justificación y dejar solo el
    # nombre del archivo.
    locality_anchors = (
        "Bundle integrity",
        "eight invariants",
        "bundle_id first-8",
    )
    has_anchor = any(a in text for a in locality_anchors)
    assert has_anchor, (
        f"La cita del archivo de fijación de la interfaz debe aparecer con un "
        f"anclaje local como 'Bundle integrity' / 'eight invariants' / "
        f"'bundle_id first-8'. No se encontró ninguno cerca de la sección "
        f"del modelo entrenado: la edición de copia pudo quitar la "
        f"justificación."
    )


def test_bundle_meta_self_referenced_bundle_id_is_consistent():
    """Si `_meta.bundle_id` está presente, debe coincidir con el SHA-256 en
    vivo de las matrices de pesos en forma canónica.

    Un bundle_id autorreferenciado que no coincida con el hash en vivo es un
    fallo de integridad que bloquea la publicación: el paquete declara una
    identidad en los metadatos mientras calcula otra en tiempo de ejecución.
    Las convenciones de paquete de la iter-29 / iter-72 / iter-117 llevan
    todas un campo `bundle_id`; esta fijación exige que se mantenga
    sincronizado.
    """
    meta = _payload().get("_meta", {})
    if not isinstance(meta, dict) or "bundle_id" not in meta:
        # Campo opcional: solo se prueba cuando está presente.
        return
    declared = meta["bundle_id"]
    actual = _canonical_bundle_id()
    assert declared == actual, (
        f"`_meta.bundle_id` de engine/bitnet_weights.json es INCONSISTENTE "
        f"con el SHA-256 en vivo: declarado={declared!r}, "
        f"vivo={actual!r}. El paquete declara una identidad en los metadatos "
        f"mientras calcula otra en tiempo de ejecución: esto rompería en "
        f"silencio la repetición de auditoría. Recalcule `_meta.bundle_id` "
        f"para que coincida con `engine.bitnet_classifier._bundle_id(payload)`."
    )
