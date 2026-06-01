"""Verifica la integridad estructural de la cohorte FHIR de demostración
de estilo Synthea.

`docs/synthea_demo_cohort.json` es un artefacto fundamental: todo hallazgo
de la demostración ("4 conflictos detectados automáticamente", cada estudio
de caso de paciente, cada rastreo de par de fármacos por paciente → entrada
del caché) depende de que este paquete esté estructuralmente bien formado.
La demostración hace varias afirmaciones sobre el paquete:

  1. "29 pacientes sintéticos · 46 identificadores de profesional"
  2. "cada profesional sanitario (Practitioner) tiene un identificador de
     profesional" (válido por Luhn)
  3. "La misma forma que habla cualquier sistema de historia clínica
     electrónica conforme con FHIR"
  4. "Todos los identificadores de profesional válidos por Luhn"
     (conformidad con la especificación FHIR R4)
  5. El marcador meta `_synthetic` de la demostración distingue los
     recursos ficticios generados por Synthea de cualquier futura
     ingesta de una historia clínica electrónica real

Sin una verificación, una deriva silenciosa podría:
  - Eliminar un recurso Patient (el tamaño se reduce, las afirmaciones de
    la demostración quedan incorrectas)
  - Añadir un Practitioner sin identificador de profesional (rompe la
    afirmación de validez por Luhn)
  - Añadir un identificador de profesional que falle Luhn (la afirmación
    se vuelve falsa)
  - Olvidar la etiqueta `_meta._synthetic` en un recurso nuevo (el guardián
    NPI_SOURCE = "DEMO_LUHN_GENERATED" es el mecanismo a prueba de fallos
    contra identificadores ficticios que contaminen búsquedas en registros
    reales)
  - Reducir las URL de evidencia / romper la cobertura de identificadores
    de cualquier forma que altere la estructura del registro

Misma clase de deriva que la verificación de cobertura de
pharmacology_flags y la verificación de integridad de la cohorte de
control negativo: un artefacto fundamental debe ser auditable desde la
batería de pruebas.
"""
from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLE = _REPO_ROOT / "docs" / "synthea_demo_cohort.json"
_DEMO = _REPO_ROOT / "docs" / "demo.html"


def _load_bundle() -> dict:
    return json.loads(_BUNDLE.read_text())


def _resources_by_type(bundle: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for entry in bundle["entry"]:
        rt = entry["resource"]["resourceType"]
        out.setdefault(rt, []).append(entry["resource"])
    return out


def _luhn_validate_npi(npi: str) -> bool:
    """Algoritmo de dígito de control de Luhn para el identificador de
    profesional (prefijo + 9 dígitos + dígito de control)."""
    if not npi.isdigit() or len(npi) != 10:
        return False
    # El identificador de profesional usa el prefijo constante 80840 +
    # 9 dígitos + dígito de control.
    digits = [int(c) for c in "80840" + npi[:9]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    expected_check = (10 - (total % 10)) % 10
    return expected_check == int(npi[9])


def test_bundle_top_level_shape():
    """El paquete debe ser un Bundle FHIR R4 (resourceType + type + lista entry)."""
    bundle = _load_bundle()
    assert bundle.get("resourceType") == "Bundle", (
        "El resourceType de nivel superior debe ser 'Bundle' para "
        "conformidad con FHIR R4."
    )
    assert bundle.get("type") in ("collection", "transaction"), (
        f"Bundle.type de FHIR debe ser 'collection' o 'transaction'; "
        f"se obtuvo {bundle.get('type')!r}"
    )
    assert isinstance(bundle.get("entry"), list), (
        "Bundle.entry debe ser una lista de recursos FHIR."
    )
    assert len(bundle["entry"]) >= 1, "El paquete debe contener al menos una entrada."


def test_patient_count_floor():
    """El número de pacientes debe ser >= 30 (mínimo).

    El crecimiento futuro de la cohorte es saludable pero debe ser
    visible: añadir un nuevo paciente sin elevar este mínimo significa que
    las afirmaciones sobre el número de pacientes en la demostración y en
    la documentación de validación clínica quedan desactualizadas en
    silencio.
    """
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    patients = by.get("Patient", [])
    assert len(patients) >= 30, (
        f"El número de pacientes ha retrocedido: actual={len(patients)}, "
        f"mínimo=30. O bien se eliminó un Patient, o bien el mínimo debe "
        f"elevarse en el mismo commit que un cambio deliberado de cohorte."
    )


def test_practitioner_count_floor():
    """El número de profesionales sanitarios debe ser >= 47 (mínimo)."""
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    practitioners = by.get("Practitioner", [])
    assert len(practitioners) >= 47, (
        f"El número de profesionales sanitarios ha retrocedido: "
        f"actual={len(practitioners)}, mínimo=47."
    )


def test_every_practitioner_has_npi():
    """Cada Practitioner debe llevar un identificador de profesional.

    La afirmación de validez por Luhn de la demostración ("cada
    Practitioner tiene un identificador de profesional validado por Luhn")
    carece de sentido si a algún Practitioner le falta su identificador.
    """
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    offenders: list[str] = []
    for prac in by.get("Practitioner", []):
        prac_id = prac.get("id", "<no-id>")
        identifiers = prac.get("identifier", [])
        npi_id = next(
            (
                i for i in identifiers
                if i.get("system") == "http://hl7.org/fhir/sid/us-npi"
            ),
            None,
        )
        if npi_id is None or not npi_id.get("value"):
            offenders.append(prac_id)
    assert not offenders, (
        f"Practitioner(s) sin identificador de profesional: {offenders}. "
        f"Añada `identifier: [{{system: 'http://hl7.org/fhir/sid/us-npi', "
        f"value: '<10-dígitos>'}}]` a cada uno."
    )


def test_every_npi_passes_cms_luhn():
    """Cada identificador de profesional de un Practitioner debe superar el
    algoritmo de dígito de control de Luhn.

    La afirmación "validado por Luhn" de la demostración se calcula con el
    algoritmo de dígito de control de Luhn: prefijo 80840 + 9 dígitos +
    dígito de control. Un identificador que falle Luhn en el paquete
    invalidaría por completo la afirmación de la demostración de que "cada
    Practitioner tiene un identificador de profesional validado por Luhn".
    """
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    offenders: list[tuple[str, str]] = []
    for prac in by.get("Practitioner", []):
        prac_id = prac.get("id", "<no-id>")
        for identifier in prac.get("identifier", []):
            if identifier.get("system") != "http://hl7.org/fhir/sid/us-npi":
                continue
            npi = identifier.get("value", "")
            if not _luhn_validate_npi(npi):
                offenders.append((prac_id, npi))
    assert not offenders, (
        f"Identificador(es) de profesional que fallan la validación de "
        f"Luhn: {offenders}. Genere reemplazos mediante "
        f"`engine.npi_registry.generate_test_npi(<seed>)`, que calcula el "
        f"dígito de control correcto."
    )


def test_every_patient_and_practitioner_marked_synthetic():
    """Cada recurso Patient + Practitioner lleva `_meta._synthetic = true`.

    Convención: Patient + Practitioner son los recursos que llevan
    metadatos *identificativos* (nombres, identificadores de profesional,
    números de historia) — esos DEBEN etiquetarse como sintéticos para que
    una futura ruta de ingesta de historia clínica electrónica nunca los
    confunda con datos de registro reales.

    Condition / Medication / Observation / AllergyIntolerance referencian
    todos un Patient sintético a través de `subject.reference` y heredan la
    procedencia sintética por ese enlace, por lo que la convención solo
    exige la etiqueta explícita en los recursos portadores de identidad.
    Una iteración futura podría extenderlo a todos los recursos mediante un
    relleno retroactivo; hasta entonces, esta verificación impone la
    convención observada existente.
    """
    bundle = _load_bundle()
    offenders: list[str] = []
    for entry in bundle["entry"]:
        resource = entry["resource"]
        rt = resource.get("resourceType")
        if rt not in ("Patient", "Practitioner"):
            continue
        rid = resource.get("id", "<no-id>")
        meta = resource.get("meta", {})
        if not meta.get("_synthetic"):
            offenders.append(f"{rt}/{rid}")
    assert not offenders, (
        f"Recurso(s) Patient/Practitioner sin `meta._synthetic = true`: "
        f"{offenders}. Los recursos portadores de identidad DEBEN "
        f"etiquetarse para que una futura ruta de ingesta de historia "
        f"clínica electrónica nunca confunda identificadores sintéticos de "
        f"la demostración con datos de registro reales de un hospital."
    )


def test_every_practitioner_has_demo_luhn_npi_source_tag():
    """Cada Practitioner.meta debe llevar npi_source = 'DEMO_LUHN_GENERATED'.

    Convención: el guardián de identificadores sintéticos. Una búsqueda
    real de identificador de profesional que encuentre
    npi_source = 'CMS_REGISTRY' trataría el valor como canónico; encontrar
    'DEMO_LUHN_GENERATED' indica a la búsqueda que NO contacte con el
    registro real de profesionales. La etiqueta es la primitiva de
    integridad que distingue los datos de la demostración de los datos
    reales.
    """
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    offenders: list[str] = []
    for prac in by.get("Practitioner", []):
        prac_id = prac.get("id", "<no-id>")
        meta = prac.get("meta", {})
        if meta.get("npi_source") != "DEMO_LUHN_GENERATED":
            offenders.append(prac_id)
    assert not offenders, (
        f"Practitioner(s) sin `meta.npi_source = 'DEMO_LUHN_GENERATED'`: "
        f"{offenders}. Necesario para evitar que una futura ruta de "
        f"ingesta de historia clínica electrónica real consulte por error "
        f"el registro real de profesionales con estos identificadores "
        f"sintéticos."
    )


def test_demo_cites_this_pin_file():
    """La demostración debe citar este archivo de verificación cerca del
    aviso de integridad de la cohorte.

    docs/demo.html incluye un aviso destacado tras la cuadrícula FHIR que
    nombra los 8 invariantes de integridad de la cohorte y referencia este
    archivo de verificación. Una futura edición de texto podría eliminar la
    referencia en silencio, haciendo que la afirmación de integridad de la
    cohorte parezca desprotegida aunque las pruebas sigan pasando
    internamente. Esta verificación impone la presencia del aviso en la
    demostración.

    Mismo patrón que las comprobaciones cruzadas "la demostración cita el
    archivo de verificación": la capa de pruebas y la superficie de cara al
    usuario se mantienen sincronizadas.
    """
    text = _DEMO.read_text()
    pin_filename = "test_synthea_cohort_integrity_pin.py"
    assert pin_filename in text, (
        f"docs/demo.html debe citar "
        f"`tests/test_engine/{pin_filename}` cerca del aviso de integridad "
        f"de la cohorte FHIR para que las afirmaciones de 29 pacientes / "
        f"46 identificadores de profesional / validación por Luhn se puedan "
        f"rastrear hasta el archivo de verificación que las impone."
    )
    # Ancla: el aviso debe aparecer con una frase reconocible para que una
    # edición de texto no pueda eliminar la justificación y dejar solo el
    # nombre del archivo (lo que aún pasaría la comprobación de subcadena de
    # arriba pero perdería el contexto legible).
    locality_anchors = (
        "8 invariantes de integridad de cohorte",
        "8 invariants",
        "integridad de la cohorte",
    )
    has_anchor = any(a in text for a in locality_anchors)
    assert has_anchor, (
        f"La cita del archivo de verificación en la demostración debe "
        f"aparecer con un ancla de localidad como '8 invariantes de "
        f"integridad de cohorte'. No se encontró ninguna cerca del aviso de "
        f"la cohorte FHIR — una edición de texto puede haber eliminado la "
        f"justificación."
    )


def test_no_real_npi_substring_collisions():
    """Ningún identificador de la demostración puede coincidir con un
    identificador de profesional real conocido públicamente (comprobación
    de prudencia).

    `engine.npi_registry.generate_test_npi(seed)` produce identificadores
    de 10 dígitos válidos por Luhn a partir de una semilla determinista; la
    aritmética del dígito de control hace que la mayoría de los
    identificadores generados caigan en el mismo espacio numérico que los
    identificadores de profesional reales registrados. No es un problema de
    privacidad a efectos de la demostración (los etiquetamos
    explícitamente como DEMO_LUHN_GENERATED), pero si un identificador
    generado coincidiera con uno real conocido del conjunto muy reducido
    divulgado públicamente en el contexto del proyecto (p. ej. el
    identificador de validación clínica 1932159530), conviene ponerlo de
    manifiesto.

    Actualmente el único identificador "real" que conocemos en el proyecto
    es el identificador de validación clínica 1932159530 de la Dra.
    Afonicheva, que reside únicamente en `docs/clinical_validation.md` (no
    en el paquete de la cohorte).
    """
    KNOWN_REAL_NPIS = frozenset({"1932159530"})
    bundle = _load_bundle()
    by = _resources_by_type(bundle)
    cohort_npis = set()
    for prac in by.get("Practitioner", []):
        for identifier in prac.get("identifier", []):
            if identifier.get("system") == "http://hl7.org/fhir/sid/us-npi":
                cohort_npis.add(identifier.get("value", ""))
    collisions = cohort_npis & KNOWN_REAL_NPIS
    assert not collisions, (
        f"Identificador(es) de la cohorte de la demostración que coinciden "
        f"con identificadores de profesional reales conocidos: "
        f"{sorted(collisions)}. El identificador de validación clínica NO "
        f"debe aparecer dentro del paquete de la demostración — reside "
        f"únicamente en docs/clinical_validation.md como atestación de un "
        f"clínico real."
    )
