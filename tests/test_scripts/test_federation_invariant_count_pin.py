"""Fija los recuentos de invariantes de la federación.

Dos afirmaciones entrelazadas aparecen en docs/demo.html,
docs/architecture.md y la demostración simulada de la federación:

  Spec  : `JointMemoryFederation.flow.mind` declara 21 invariantes
          tipados de ejecución (el contrato completo de la federación).
  Demo  : `scripts/federation_mock_demo.py` ejercita LOS 21 de extremo
          a extremo. La iteración 2026-05-11 cableó los 5 invariantes de
          sellado X25519 (ida y vuelta de `SealedEnvelope` + `_x25519_seal` /
          `_x25519_open` en proceso) para que la demostración simulada
          refleje el sobre criptográfico de formato de cable del
          transporte de cable HTTP de federación v4 (mind-mem `main`
          16a3e25, etiqueta PyPI v4.0.x pendiente).

Esta prueba fija ambos números para que cualquier edición futura del
contrato que añada o elimine invariantes —o cualquier cambio de
prueba/demostración que rebase el recuento ejercitado— falle la puerta
hasta que los documentos + el contrato + la demostración se actualicen
juntos.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FLOW = _REPO_ROOT / "flows" / "JointMemoryFederation.flow.mind"
_DEMO_HTML = _REPO_ROOT / "docs" / "demo.html"
_ARCH = _REPO_ROOT / "docs" / "architecture.md"

# Iteración 135 (T1 ronda 27): la misma clase de deriva que la iteración
# 132 detectó para la fijación del transporte de cable v3.9 también vivía
# aquí. Tres documentos adicionales de cara al usuario mentían sobre el
# recuento de invariantes del flujo (afirmando "16 invariants" cuando el
# recuento en vivo es 21):
#
#   * docs/clinical_validation.md   (resumen de validación multicéntrica)
#   * docs/federated_memory.md      (estado de la arquitectura de federación)
#
# Estos quedaban FUERA del alcance de la fijación de la iteración 22
# (demo.html / architecture.md), así que la misma confusión
# sobrevivió sin detectarse. La iteración 135 amplía el alcance a toda la
# superficie de federación de cara al usuario.
_USER_FACING_FED_DOCS = (
    _REPO_ROOT / "docs" / "demo.html",
    _REPO_ROOT / "docs" / "architecture.md",
    _REPO_ROOT / "docs" / "clinical_validation.md",
    _REPO_ROOT / "docs" / "federated_memory.md",
)

# Constantes fijadas. Cuando se añade o elimina un `invariant` del
# contrato del flujo, actualizar el número correspondiente Y cada documento
# que lo nombre en el mismo commit.
_EXPECTED_SPEC_INVARIANT_COUNT = 21    # contrato tipado completo
_EXPECTED_DEMO_INVARIANT_COUNT = 21    # ejercitado por federation_mock_demo (sellado/apertura X25519 aterrizó 2026-05-11)


def _count_invariants_in_flow() -> int:
    """Cuenta las declaraciones `invariant <expr>` en el contrato .flow.mind."""
    text = _FLOW.read_text()
    return sum(
        1 for line in text.splitlines() if re.match(r"\s*invariant\s+", line)
    )


def _count_demo_invariants() -> int:
    """Cuenta las entradas del diccionario INVARIANT_DESCRIPTIONS de la demostración."""
    import sys
    sys.path.insert(0, str(_REPO_ROOT))
    from scripts.federation_mock_demo import INVARIANT_DESCRIPTIONS  # noqa

    return len(INVARIANT_DESCRIPTIONS)


def test_flow_contract_declares_pinned_invariant_count():
    n = _count_invariants_in_flow()
    assert n == _EXPECTED_SPEC_INVARIANT_COUNT, (
        f"El recuento de invariantes del contrato del flujo se desvió: en vivo={n}, "
        f"fijado={_EXPECTED_SPEC_INVARIANT_COUNT}. "
        f"Actualizar _EXPECTED_SPEC_INVARIANT_COUNT + cada referencia en documentos "
        f"(docs/demo.html, docs/architecture.md) en el "
        f"mismo commit."
    )


def test_demo_exercises_pinned_invariant_count():
    n = _count_demo_invariants()
    assert n == _EXPECTED_DEMO_INVARIANT_COUNT, (
        f"El recuento de INVARIANT_DESCRIPTIONS de la demostración se desvió: en vivo={n}, "
        f"fijado={_EXPECTED_DEMO_INVARIANT_COUNT}"
    )


def test_dashboard_claims_match_pinned_counts():
    """Todo documento de federación de cara al usuario debe citar el recuento fijado de la especificación.

    Alcance de la iteración 22: demo.html + architecture.md.
    Ampliación de alcance de la iteración 135: clinical_validation.md,
    federated_memory.md: tres documentos de cara al
    usuario que mentían sobre el recuento de invariantes del flujo
    (afirmaban "16" cuando el recuento en vivo es 21). Misma clase de deriva
    que la extensión de la fijación del transporte de cable v3.9 de la
    iteración 132.
    """
    spec_str = f"{_EXPECTED_SPEC_INVARIANT_COUNT} typed"  # "21 typed invariants"
    demo_html = _DEMO_HTML.read_text()
    demo_str = (
        f"{_EXPECTED_DEMO_INVARIANT_COUNT} / "
        f"{_EXPECTED_DEMO_INVARIANT_COUNT} invariants"
    )  # "16 / 16 invariants PASS"

    for path in _USER_FACING_FED_DOCS:
        if not path.exists():
            continue
        text = path.read_text()
        rel = path.relative_to(_REPO_ROOT)
        assert spec_str in text, (
            f"{rel} debe contener {spec_str!r} (el flujo en vivo declara "
            f"{_EXPECTED_SPEC_INVARIANT_COUNT} invariantes tipados; este documento "
            f"está en la superficie de federación de cara al usuario y un recuento "
            f"obsoleto sería una fabricación silenciosa del contrato de la federación)."
        )
    assert demo_str in demo_html, f"docs/demo.html debe contener {demo_str!r}"


# Frases que históricamente aparecían en documentos de cara al usuario
# afirmando que el flujo tiene solo 16 invariantes: eso era cierto en una
# iteración anterior, pero el recuento ha crecido desde entonces a 21. Un
# "16 invariants" / "16 typed" sin acompañar en cualquiera de los
# documentos de cara al usuario es una regresión a menos que vaya
# emparejado con una desambiguación explícita "21 typed" en el mismo documento.
_FORBIDDEN_BARE_PHRASES = (
    "16 invariants",          # "16 invariants" sin acompañar y sin desambiguador del 21
    "16 typed invariants",
    "16 typed runtime invariants",
)


def test_no_unscoped_16_invariant_claim_in_user_facing_docs():
    """Todo documento de federación de cara al usuario que mencione "16 invariants"
    debe mencionar también "21 typed" en el mismo archivo (la desambiguación
    que evita que el recuento sin acompañar parezca el contrato completo).

    El diccionario `INVARIANT_DESCRIPTIONS` de la demostración en
    scripts/federation_mock_demo.py tiene legítimamente 16 entradas (el
    subconjunto ejercitado en vivo); ese es un archivo Python, no está en el
    alcance de cara al usuario. Asimismo, el contexto de medición de
    `arch_mind_federation_audit.md` dice "16 of 21", lo cual está bien.
    """
    for path in _USER_FACING_FED_DOCS:
        if not path.exists():
            continue
        text = path.read_text()
        rel = path.relative_to(_REPO_ROOT)
        for phrase in _FORBIDDEN_BARE_PHRASES:
            if phrase in text and f"{_EXPECTED_SPEC_INVARIANT_COUNT} typed" not in text:
                raise AssertionError(
                    f"{rel} contiene {phrase!r} sin acompañar y sin la "
                    f"desambiguación requerida '21 typed'. O bien reformular "
                    f"la frase para nombrar el recuento completo del flujo "
                    f"({_EXPECTED_SPEC_INVARIANT_COUNT} invariantes tipados) "
                    f"o emparejar el 16 con contexto explícito 'of 21' / '21 typed' "
                    f"(la desambiguación de la iteración 22: 21 declarados, "
                    f"16 ejercitados por la demostración simulada, 5 invariantes "
                    f"de sellado X25519 a la espera de MIC@2)."
                )


def test_demo_cites_this_pin_file():
    """Extensión de superficie de la iteración 136: demo.html debe citar
    este archivo de fijación cerca de la superficie de invariantes de la
    federación, con un ancla de localidad que nombre el alcance de 6
    documentos cruzados (la ampliación de alcance de la iteración 135).

    Mismo patrón de `la demostración cita el archivo de fijación` que las
    iteraciones 110 (BitNet en solitario), 115 (clase de diseño de BitNet),
    121 (cohorte de control negativo), 126 (cohorte Synthea), 131
    (integridad del paquete BitNet). El propósito del patrón: exponer la
    salvaguarda mecánica justo donde vive la afirmación de cara al usuario,
    de modo que quien lea la tarjeta de la federación vea que el recuento de
    21 está protegido por una prueba, no es solo texto de documentación.
    """
    demo = _DEMO_HTML.read_text()
    pin_basename = "test_federation_invariant_count_pin.py"
    assert pin_basename in demo, (
        f"docs/demo.html debe citar el nombre del archivo de fijación {pin_basename!r} "
        f"cerca de la tarjeta de JointMemoryFederation para que la superficie de "
        f"integridad de recuento entre documentos tenga un puntero a la salvaguarda mecánica."
    )
    # Ancla de localidad: al menos una de estas frases debe aparecer en
    # demo.html para que la cita quede anclada a la superficie de la
    # iteración 135, no soltada al azar.
    locality_anchors = (
        "Cross-doc invariant-count integrity",
        "all 6 user-facing federation docs",
        "iter-135 scope-expansion",
    )
    assert any(a in demo for a in locality_anchors), (
        f"demo.html cita el archivo de fijación pero le falta el ancla de "
        f"localidad de ampliación de alcance de la iteración 135. Una de estas "
        f"frases debe aparecer cerca de la cita: {locality_anchors!r}"
    )


def test_demo_to_spec_gap_closed():
    """A fecha de 2026-05-11 la brecha de 5 invariantes está CERRADA: la
    demostración simulada ejercita los 21 invariantes en proceso mediante la
    ida y vuelta de SealedEnvelope + `_x25519_seal` / `_x25519_open`. El panel
    de la demostración debe citar el cierre (sin la divulgación obsoleta
    "5 X25519 await wire")."""
    demo_html = _DEMO_HTML.read_text()
    gap = _EXPECTED_SPEC_INVARIANT_COUNT - _EXPECTED_DEMO_INVARIANT_COUNT  # 0
    assert gap == 0, (
        f"La brecha entre demostración y especificación es {gap}; debería ser 0 tras "
        f"aterrizar la ida y vuelta X25519 en proceso de 2026-05-11."
    )
    # La divulgación obsoleta "5 X25519 sealing invariants are" no debe
    # permanecer en el panel: ahora sería una afirmación incorrecta.
    assert "5 X25519 sealing invariants are" not in demo_html, (
        "docs/demo.html todavía contiene la divulgación obsoleta de 5 invariantes "
        "'await wire' de antes de que aterrizara la ida y vuelta X25519 en proceso "
        "de 2026-05-11. Eliminar la divulgación (la brecha está cerrada)."
    )
