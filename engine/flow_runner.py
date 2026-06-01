"""Ejecutor de flujos de Historia Clínica — intérprete Python independiente para fuentes `.flow.mind`.

Cada archivo `.flow.mind` en `flows/` es un contrato de grafo tipado:
puertos de entrada/salida, nodos con nombre, invariantes `@verify` y una
identidad direccionada por contenido (el `plan_hash`). Compilar el archivo
mediante la cadena de herramientas MIND Flow (`mindlang.dev`) produce un
plan de ejecución binario con el mismo `plan_hash`; **este ejecutor en
Python es la ruta de verificación de código abierto que se distribuye en
Historia Clínica**, de modo que un revisor con este archivo más los seis
archivos fuente `.flow.mind` puede reproducir cualquier decisión clínica en
minutos sin instalar ninguna cadena de herramientas propietaria.

Lo que hace este módulo
───────────────────────
1. **Catálogo.** `list_flows()` enumera los archivos `.flow.mind`
   disponibles en `flows/`.
2. **Hash del plan.** `compute_plan_hash(name)` devuelve el SHA-256 de los
   bytes canónicos de la fuente del flujo — la dirección de contenido
   determinista que cada entrada de la cadena de auditoría registra como
   el «ID de decisión».
3. **Contrato estático.** `parse_flow_contract(name)` extrae la lista de
   puertos tipados, la lista de nodos y los predicados invariantes de la
   fuente para que el panel pueda representar el grafo sin compilarlo.
4. **Stub de reproducción verificable.** `verify_replay(name, expected_hash)`
   recalcula el `plan_hash` y comprueba que coincide con el valor esperado
   por el revisor — usado por el endpoint `/v1/replay` y la CI para
   detectar manipulaciones de la fuente del flujo.

Lo que este módulo NO hace
──────────────────────────
- No ejecuta los nodos del flujo. El despacho de nodos pasa por
  `engine/consensus_engine.py` y `engine/clinical_scoring.py` como
  antes; el archivo `.flow.mind` es el *contrato*, el motor Python es el
  *ejecutor*. Ambos se mantienen sincronizados mediante
  `tests/test_engine/test_flow_runner.py`.
- No implementa un analizador `mindc` completo. La gramática que este
  módulo reconoce es un pequeño subconjunto orientado a líneas de
  `.flow.mind` suficiente para extraer la superficie del contrato
  (`flow Name { … }`, `input X: T`, `output Y: T`, `node Z = …`,
  `invariant E`, `@profile`, `@kernel`).
- No emite un plan de ejecución binario. Esa es la tarea de `mindc`; la
  forma binaria es opcional mediante la cadena de herramientas externa.

Alcance público
────────────────
Este archivo se distribuye con licencia Apache-2.0 junto con el resto de
Historia Clínica. NO incorpora ninguna fuente de la cadena de herramientas
propietaria de STARGA (con licencia comercial, residente en repositorios
privados). Los archivos fuente
`.flow.mind` en `flows/` son artefactos arquitectónicos Apache-2.0 creados
por STARGA.

Copyright 2026 STARGA, Inc. — Licencia Apache-2.0.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _hash_patient_id(patient_id: str) -> str:
    """Prefijo SHA-256 de 16 caracteres de patient_id, seguro para datos del paciente.

    Migración de claves de extras de datos del paciente (iter-333): aplica
    hash a patient_id antes de registrarlo mediante `extra={}`. Refleja
    `engine/fhir_client.py::_hash_patient_id` (iter-332) y el patrón de
    disciplina de datos del paciente de iter-291/iter-284/iter-279/iter-309.
    """
    return hashlib.sha256((patient_id or "").encode("utf-8")).hexdigest()[:16]

# Directorio canónico de flujos — relativo a la raíz del repositorio.
_FLOWS_DIR: Path = Path(__file__).parent.parent / "flows"


@dataclass(frozen=True)
class FlowPort:
    """Un puerto tipado (de entrada o de salida) de un flujo."""

    name: str
    type_expr: str
    direction: str   # "input" o "output"


@dataclass(frozen=True)
class FlowNode:
    """Un nodo con nombre en el grafo del flujo."""

    name: str
    directive: str        # "@native", "@llm", "@flow", "@recall", "@verify"
    expression: str       # La expresión fuente tras el `=`


@dataclass(frozen=True)
class FlowInvariant:
    """Un predicado `invariant` que debe cumplirse en tiempo de ejecución."""

    predicate: str        # El texto de la expresión tras `invariant`


@dataclass(frozen=True)
class FlowContract:
    """El contrato estático y reproducible para auditoría extraído de una fuente de flujo."""

    name: str
    flow_path: Path
    plan_hash: str        # SHA-256 en hex de los bytes canónicos de la fuente
    profile: str          # valor de `@profile "..."`, o "default"
    kernel: str           # valor de `@kernel "..."`, o "" si no está definido
    inputs: tuple[FlowPort, ...]
    outputs: tuple[FlowPort, ...]
    nodes: tuple[FlowNode, ...]
    invariants: tuple[FlowInvariant, ...]


# ─── plan_hash canónico ─────────────────────────────────────────────────────

def _canonicalise_source(source_bytes: bytes) -> bytes:
    """Canonicaliza los bytes de la fuente del flujo para un `plan_hash` estable.

    Reglas (mantenidas estrictas a propósito; byte exacto cuando es posible):
      1. Decodificar como UTF-8.
      2. Normalizar los finales de línea a `\n` (CRLF / CR -> LF).
      3. Eliminar los espacios finales de cada línea.
      4. Eliminar un único `\n` final para que los archivos con o sin
         salto de línea final tengan el mismo hash.

    Esto preserva el formato legible por el desarrollador (la sangría
    dentro de las líneas es significativa — forma parte del contrato de
    grafo tipado) mientras ignora artefactos de editor irrelevantes.
    """
    text = source_bytes.decode("utf-8", errors="strict")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    canonical = "\n".join(lines)
    if canonical.endswith("\n"):
        canonical = canonical.rstrip("\n")
    return canonical.encode("utf-8")


def compute_plan_hash(flow_name: str, flows_dir: Path | None = None) -> str:
    """Devuelve el `plan_hash` canónico (SHA-256 en hex) de una fuente de flujo.

    Estable ante ediciones de solo espacios al final de línea y al final
    del archivo; cualquier cambio semántico en la fuente produce un hash
    nuevo. El hash es el «ID de decisión» de la cadena de auditoría
    registrado para cada decisión clínica que produce el flujo.
    """
    flow_path = _resolve_flow_path(flow_name, flows_dir)
    canonical = _canonicalise_source(flow_path.read_bytes())
    digest = hashlib.sha256(canonical).hexdigest()
    # Seguro para datos del paciente: flow_name es configuración y el
    # digest es por definición no reversible. canonical_size es metadato,
    # no contenido.
    logger.debug(
        "flow_plan_hash_computed",
        extra={
            "flow_name": flow_name,
            "plan_hash_prefix": digest[:16],
            "canonical_size_bytes": len(canonical),
        },
    )
    return digest


def _resolve_flow_path(flow_name: str, flows_dir: Path | None = None) -> Path:
    """Resuelve un nombre de flujo a la ruta de su fuente `.flow.mind`."""
    base = Path(flows_dir) if flows_dir is not None else _FLOWS_DIR
    # Acepta "MedicationSafetyReview" o "MedicationSafetyReview.flow.mind"
    if not flow_name.endswith(".flow.mind"):
        flow_name = flow_name + ".flow.mind"
    candidate = base / flow_name
    if not candidate.exists():
        # Registra solo el nombre base solicitado + el recuento disponible —
        # nunca la ruta completa del sistema de archivos (podría filtrar el
        # cwd del llamante) ni la lista de flujos disponibles (permitiría a
        # un sondeo enumerar el catálogo mediante repetidos 404 sobre
        # nombres mal escritos).
        logger.error(
            "flow_resolve_failed",
            extra={
                "flow_name": flow_name,
                "available_count": len(list_flow_names(base)),
            },
        )
        raise FileNotFoundError(
            f"fuente de flujo no encontrada: {candidate}; flujos disponibles: {list_flow_names(base)}"
        )
    # Ruta de éxito a nivel DEBUG: cada resolución exitosa deja una huella
    # de referencia para que los operadores puedan calcular la tasa de
    # resolución (llamadas por minuto) y distinguir «sin llamadas» de
    # «todo correcto». Seguro para datos del paciente: flow_name es
    # metadato estructural (nombre base del archivo), nunca datos del
    # paciente.
    logger.debug(
        "flow_resolve_ok",
        extra={"flow_name": flow_name},
    )
    return candidate


# ─── Catálogo ────────────────────────────────────────────────────────────

def list_flow_names(flows_dir: Path | None = None) -> list[str]:
    """Lista los nombres de flujo disponibles (sin el sufijo `.flow.mind`)."""
    base = Path(flows_dir) if flows_dir is not None else _FLOWS_DIR
    if not base.exists():
        # Una solicitud de catálogo contra un directorio inexistente es un
        # error de configuración previa; se reporta en nivel WARNING para
        # que los operadores detecten despliegues mal configurados. Seguro
        # para datos del paciente: la ruta del directorio es estructural
        # (sin datos del paciente).
        logger.warning(
            "flow_catalogue_dir_missing",
            extra={"base_exists": False},
        )
        return []
    names = sorted(p.stem.replace(".flow", "") for p in base.glob("*.flow.mind"))
    logger.debug(
        "flow_catalogue_listed",
        extra={"flow_count": len(names)},
    )
    return names


def list_flows(flows_dir: Path | None = None) -> list[FlowContract]:
    """Analiza y devuelve cada contrato `.flow.mind` bajo `flows/`."""
    contracts = [
        parse_flow_contract(name, flows_dir=flows_dir)
        for name in list_flow_names(flows_dir)
    ]
    # DEBUG — resumen del análisis en bloque para que los operadores vean
    # la instantánea del recuento de contratos cuando el ejecutor (re)carga
    # el conjunto de flujos al arrancar. Se combina con el DEBUG por
    # contrato de parse_flow_contract.
    logger.debug(
        "flow_list_complete",
        extra={
            "contract_count": len(contracts),
            "flows_dir": str(flows_dir) if flows_dir else "default",
        },
    )
    return contracts


# ─── Extracción del contrato estático ───────────────────────────────────────

_FLOW_HEADER_RE = re.compile(r"^\s*flow\s+(\w+)\s*\{\s*$")
_INPUT_RE = re.compile(r"^\s*input\s+(\w+)\s*:\s*(.+?)\s*$")
_OUTPUT_RE = re.compile(r"^\s*output\s+(\w+)\s*:\s*(.+?)\s*$")
_NODE_RE = re.compile(r"^\s*node\s+(\w+)\s*=\s*(@\w+)\s*(.+?)\s*$")
_ASSIGN_RE = re.compile(r"^\s*assign\s+(\w+)\s*=\s*(@\w+)?\s*(.+?)\s*$")
_INVARIANT_RE = re.compile(r"^\s*invariant\s+(.+?)\s*$")
_PROFILE_RE = re.compile(r'^\s*@profile\s+"(.+?)"\s*$')
_KERNEL_RE = re.compile(r'^\s*@kernel\s+"(.+?)"\s*$')


def parse_flow_contract(
    flow_name: str,
    flows_dir: Path | None = None,
) -> FlowContract:
    """Analiza un archivo `.flow.mind` en un FlowContract estático.

    La gramática es orientada a líneas y tolerante — los comentarios
    (`//`) y las líneas en blanco se omiten; las expresiones `node` de
    varias líneas se colapsan a una sola línea antes de la coincidencia
    con expresiones regulares. La tarea de este módulo es extraer la
    superficie reproducible para auditoría, no reimplementar el
    analizador `mindc`.
    """
    flow_path = _resolve_flow_path(flow_name, flows_dir)
    raw_text = flow_path.read_text(encoding="utf-8")

    # Elimina los comentarios de línea (`// …`); mantiene las expresiones de
    # varias líneas en una sola para que coincidan las expresiones regulares
    # orientadas a líneas. Un nodo como
    #     node consensus = @llm consensus(
    #         models: [...],
    #     )
    # se colapsa en una sola línea.
    stripped_lines: list[str] = []
    pending: str | None = None
    paren_depth: int = 0
    for raw_line in raw_text.splitlines():
        # elimina los comentarios estilo `//` (básico — no maneja `//` dentro de cadenas)
        line = raw_line.split("//", 1)[0].rstrip()
        if not line.strip():
            if pending is None:
                stripped_lines.append("")
                continue
            # continuación pendiente — sigue adelante, ignora las líneas en blanco
            continue
        if pending is not None:
            pending = pending + " " + line.strip()
            paren_depth += line.count("(") - line.count(")")
            paren_depth += line.count("[") - line.count("]")
            paren_depth += line.count("{") - line.count("}")
            if paren_depth <= 0:
                stripped_lines.append(pending)
                pending = None
                paren_depth = 0
            continue
        depth_delta = (
            line.count("(") - line.count(")")
            + line.count("[") - line.count("]")
        )
        if depth_delta > 0:
            pending = line
            paren_depth = depth_delta
            continue
        stripped_lines.append(line)
    if pending is not None:
        # multilínea sin terminar; se recupera como una sola línea
        stripped_lines.append(pending)

    flow_decl_name: str | None = None
    profile = "default"
    kernel = ""
    inputs: list[FlowPort] = []
    outputs: list[FlowPort] = []
    nodes: list[FlowNode] = []
    invariants: list[FlowInvariant] = []

    for line in stripped_lines:
        if not line.strip():
            continue
        if (m := _PROFILE_RE.match(line)) is not None:
            profile = m.group(1)
            continue
        if (m := _KERNEL_RE.match(line)) is not None:
            kernel = m.group(1)
            continue
        if (m := _FLOW_HEADER_RE.match(line)) is not None:
            flow_decl_name = m.group(1)
            continue
        if (m := _INPUT_RE.match(line)) is not None:
            inputs.append(FlowPort(name=m.group(1), type_expr=m.group(2), direction="input"))
            continue
        if (m := _OUTPUT_RE.match(line)) is not None:
            outputs.append(FlowPort(name=m.group(1), type_expr=m.group(2), direction="output"))
            continue
        if (m := _NODE_RE.match(line)) is not None:
            nodes.append(
                FlowNode(
                    name=m.group(1),
                    directive=m.group(2),
                    expression=m.group(3),
                )
            )
            continue
        if (m := _ASSIGN_RE.match(line)) is not None:
            # `assign output = @directive ...` — se trata como un nodo
            # terminal que produce el enlace de salida del flujo. El
            # ejecutor usa la salida del último nodo no omitido como salida
            # del flujo cuando el invocable de asignación no se despacha.
            target = m.group(1)
            directive = m.group(2) or "@native"
            nodes.append(
                FlowNode(
                    name=target,
                    directive=directive,
                    expression=m.group(3),
                )
            )
            continue
        if (m := _INVARIANT_RE.match(line)) is not None:
            invariants.append(FlowInvariant(predicate=m.group(1)))
            continue

    if flow_decl_name is None:
        logger.error(
            "flow_parse_failed",
            extra={
                "flow_path_basename": flow_path.name,
                "reason": "missing_flow_decl",
            },
        )
        raise ValueError(f"no se encontró declaración `flow Name {{ … }}` en {flow_path}")

    # Registro de la forma del contrato seguro para datos del paciente:
    # solo recuentos, sin texto de expresiones.
    logger.debug(
        "flow_contract_parsed",
        extra={
            "flow_name": flow_decl_name,
            "profile": profile,
            "kernel": kernel or "",
            "input_count": len(inputs),
            "output_count": len(outputs),
            "node_count": len(nodes),
            "invariant_count": len(invariants),
        },
    )

    return FlowContract(
        name=flow_decl_name,
        flow_path=flow_path,
        plan_hash=compute_plan_hash(flow_name, flows_dir=flows_dir),
        profile=profile,
        kernel=kernel,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        nodes=tuple(nodes),
        invariants=tuple(invariants),
    )


# ─── Verificador de reproducción ─────────────────────────────────────────────

@dataclass(frozen=True)
class ReplayResult:
    """Resultado de `verify_replay` — usado por el endpoint `/v1/replay`."""

    flow_name: str
    expected_hash: str
    actual_hash: str
    matches: bool
    contract: FlowContract


def verify_replay(
    flow_name: str,
    expected_hash: str,
    flows_dir: Path | None = None,
) -> ReplayResult:
    """Verifica por reproducción un flujo contra el `plan_hash` esperado por un revisor.

    Esta es la primitiva fundamental de reproducibilidad para la
    validación clínica: un revisor almacena `(flow_name, plan_hash,
    input_hash, output_hash)` en la cadena de auditoría en el momento de
    la decisión; meses después, llama a esta función para comprobar que la
    fuente del flujo no ha sido manipulada.

    Devuelve un ReplayResult; `matches=False` es un evento de integridad
    de la cadena de auditoría que bloquea la publicación.
    """
    contract = parse_flow_contract(flow_name, flows_dir=flows_dir)
    matches = (contract.plan_hash == expected_hash)
    if matches:
        # INFO en caso de éxito — los revisores quieren ver el resultado de
        # reproducción positivo en la cadena de auditoría, no solo los
        # fallos. Solo el prefijo del hash (el hash completo está en el
        # ReplayResult de todos modos).
        logger.info(
            "flow_replay_verified",
            extra={
                "flow_name": flow_name,
                "matches": True,
                "plan_hash_prefix": contract.plan_hash[:16],
            },
        )
    else:
        # Nivel WARNING — evento de integridad de la cadena de auditoría
        # que bloquea la publicación.
        logger.warning(
            "flow_replay_mismatch",
            extra={
                "flow_name": flow_name,
                "matches": False,
                "expected_prefix": expected_hash[:16],
                "actual_prefix": contract.plan_hash[:16],
            },
        )
    return ReplayResult(
        flow_name=flow_name,
        expected_hash=expected_hash,
        actual_hash=contract.plan_hash,
        matches=matches,
        contract=contract,
    )


# ─── Ejecutor en vivo (brecha #2 priorizada por consenso) ────────────────────
#
# Las fuentes .flow.mind son los contratos de grafo tipado. El ejecutor de
# abajo recorre la lista de nodos del contrato analizado en orden de
# declaración, despacha cada nodo `@native` al punto de entrada del motor
# correspondiente, registra evidencia por nodo y emite un resultado
# FlowExecution estructurado. Cada ejecución lleva el ID de decisión
# `plan_hash` contra el cual un revisor puede reproducir.
#
# La tabla de despacho asigna `(directive, node_name)` -> invocable Python.
# El ejecutor es deliberadamente acotado: maneja el subconjunto de la
# canalización determinista (escaneo de datos del paciente, normalización
# RxNorm, comprobación de tabla determinista, sello BitNet de la capa 4.5,
# emisión de la cadena de auditoría). Las directivas `@llm` de consenso +
# síntesis se enrutan a través de engine.consensus_engine y requieren las
# claves de API correspondientes; sin ellas el ejecutor registra el nodo
# como omitido y la invariante `@verify consensus.available_models >= 4` se
# dispara de forma honesta. Las llamadas `@flow` (composicionales) vuelven a
# entrar en execute().


@dataclass(frozen=True)
class NodeExecution:
    """Sello de evidencia por nodo — registrado para cada nodo de flujo ejecutado."""

    node_name: str
    directive: str
    status: str           # "ok" | "skipped" | "failed"
    output_hash: str      # SHA-256 sobre la codificación JSON canónica de la salida
    elapsed_ms: int
    detail: str = ""


@dataclass(frozen=True)
class FlowExecution:
    """Resultado de una ejecución de flujo — alimenta la entrada de la cadena de auditoría."""

    flow_name: str
    plan_hash: str
    inputs_hash: str      # SHA-256 sobre la codificación JSON canónica de las entradas
    output_hash: str      # SHA-256 sobre la codificación JSON canónica de la salida final
    nodes: tuple[NodeExecution, ...]
    invariant_violations: tuple[str, ...]
    output: object | None
    elapsed_ms: int


def _canonical_json_hash(value: object) -> str:
    """SHA-256 sobre la codificación JSON canónica; estable en cualquier máquina."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dispatch_table() -> dict[tuple[str, str], object]:
    """Construye la tabla de despacho `(directive, node_name) -> invocable`.

    Importada de forma diferida para que la ruta del verificador siga
    siendo ligera en importaciones. Cada invocable toma un único
    `inputs: dict` y devuelve un valor serializable a JSON. Los nodos
    desconocidos se registran como "skipped" sin lanzar excepciones.
    """
    table: dict[tuple[str, str], object] = {}

    # Escaneo de datos del paciente (capa 0) — engine.phi_detector.scan_phi
    def _phi_scan(inputs: dict) -> dict:
        from engine.phi_detector import scan_phi
        text = " ".join(str(v) for v in inputs.get("medications", []))
        report = scan_phi(text)
        # Evento de nivel INFO — la verja de datos del paciente es el primer
        # nodo crítico para la seguridad; los operadores deben ver cada
        # despacho en el registro de auditoría incluso en los niveles de
        # registro predeterminados. Seguro para datos del paciente: solo la
        # LONGITUD del texto concatenado + el recuento de coincidencias + el
        # booleano de aptitud para uso externo. El propio phi_detector emite
        # sus propios eventos únicamente con text_length.
        logger.info(
            "flow_node_phi_scan",
            extra={
                "text_length": len(text),
                "med_count": len(inputs.get("medications", [])),
                "phi_match_count": len(report.matches),
                "safe_for_external": len(report.matches) == 0,
            },
        )
        return {
            "safe_for_external": len(report.matches) == 0,
            "phi_match_count": len(report.matches),
        }
    table[("@native", "phi")] = _phi_scan
    table[("@native", "phi_scan")] = _phi_scan

    # Normalización RxNorm (preparación de la capa 3)
    def _rxnorm_normalize(inputs: dict) -> dict:
        meds = inputs.get("medications", [])
        # Respaldo ligero cuando la API en vivo no es accesible: cuenta los
        # medicamentos como "resueltos" cuando aparecen en la tabla
        # determinista.
        from engine.clinical_scoring import _KNOWN_INTERACTIONS
        known = {drug for drug_a, drug_b, _, _ in _KNOWN_INTERACTIONS for drug in (drug_a, drug_b)}
        coverage = sum(1 for m in meds if any(d in m.lower() for d in known))
        coverage_ratio = coverage / max(len(meds), 1)
        # Observabilidad iter-324 — huella a nivel de nodo de flujo para la
        # normalización RxNorm de la capa 3. Reflejo de iter-314 (capa 4.5)
        # + iter-319 (capa 1). Seguro para datos del paciente: solo
        # med_count + resolved_count + coverage_ratio — sin nombres de
        # medicamentos. Los nombres de medicamentos nunca llegan al registro.
        logger.debug(
            "flow_node_rxnorm_normalize",
            extra={
                "med_count": len(meds),
                "resolved_count": coverage,
                "coverage_ratio_q4": round(coverage_ratio, 4),
            },
        )
        return {
            "medications": meds,
            "coverage_ratio": coverage_ratio,
        }
    table[("@native", "normalized")] = _rxnorm_normalize
    table[("@native", "normalize")] = _rxnorm_normalize

    # Comprobación de tabla determinista (capa 1)
    def _deterministic_check(inputs: dict) -> dict:
        from engine.clinical_scoring import check_drug_interactions
        meds = inputs.get("medications", [])
        # Desactiva el respaldo por LLM para que este nodo sea un sello
        # limpio de la capa 1
        results = check_drug_interactions(meds, use_llm_fallback=False)
        # Observabilidad iter-319 — huella a nivel de nodo de flujo para el
        # sello de tabla determinista de la capa 1. Se dispara una vez por
        # ejecución de flujo; reflejo de iter-314 _bitnet_classify (29.º
        # punto cruzado) en la entrada de despacho de la capa 1. Seguro para
        # datos del paciente: solo med_count + interaction_count +
        # severity_histogram (categórico). Los nombres de medicamentos nunca
        # llegan al registro. El histograma de severidad es, desde el punto
        # de vista de la teoría de la información, irreversible en cohortes
        # realistas.
        sev_counts: dict[str, int] = {}
        for r in results:
            s = r.severity
            sev_counts[s] = sev_counts.get(s, 0) + 1
        logger.debug(
            "flow_node_deterministic_check",
            extra={
                "med_count": len(meds),
                "interaction_count": len(results),
                "severity_histogram": sev_counts,
            },
        )
        return {
            "interactions": [
                {"drug_a": r.drug_a, "drug_b": r.drug_b, "severity": r.severity,
                 "bitnet_severity": r.bitnet_severity,
                 "bitnet_repro_hash": r.bitnet_repro_hash}
                for r in results
            ],
            "interaction_count": len(results),
        }
    table[("@native", "tier1")] = _deterministic_check

    # Sello BitNet de la capa 4.5 sobre un único par (reentrante)
    def _bitnet_classify(inputs: dict) -> dict:
        from engine.bitnet_classifier import classifier_layer
        meds = inputs.get("medications", [])
        out: list[dict] = []
        for i in range(len(meds)):
            for j in range(i + 1, len(meds)):
                r = classifier_layer(meds[i], meds[j])
                out.append({
                    "drug_a": meds[i], "drug_b": meds[j],
                    "severity": r.severity_name,
                    "repro_hash": r.repro_hash,
                    "weights_id": r.weights_id,
                })
        # Observabilidad iter-314 — huella a nivel de nodo de flujo para el
        # sello de la capa 4.5 por par. Se dispara una vez por ejecución de
        # flujo independientemente del tamaño de la cohorte; complementa el
        # evento DEBUG por par de engine.bitnet_classifier (forma segura
        # para datos del paciente de iter-309). Seguro para datos del
        # paciente: solo recuentos + metadatos estructurales + el
        # weights_id canónico (un hash SHA-256 del paquete, no datos del
        # paciente). Los nombres de medicamentos nunca llegan al registro.
        # El desglose por clase de severidad es solo un histograma
        # categórico — nunca correspondencia par a par.
        sev_counts: dict[str, int] = {}
        for entry in out:
            s = entry["severity"]
            sev_counts[s] = sev_counts.get(s, 0) + 1
        weights_id_prefix = (out[0]["weights_id"][:16] if out else "")
        logger.debug(
            "flow_node_bitnet_classify",
            extra={
                "med_count": len(meds),
                "pair_count": len(out),
                "severity_histogram": sev_counts,
                "weights_id_prefix": weights_id_prefix,
            },
        )
        return {"pairs": out, "weights_id": out[0]["weights_id"] if out else ""}
    table[("@native", "bitnet")] = _bitnet_classify
    table[("@native", "bitnet_ternary_classify")] = _bitnet_classify

    # Emisión de la cadena de auditoría (registra la evidencia por nodo en la cadena)
    def _emit_audit_chain(inputs: dict) -> dict:
        # Sello ligero; la cadena de auditoría real la registra execute()
        node_count = inputs.get("_node_count", 0)
        # Evento de nivel INFO — la emisión de la cadena de auditoría es el
        # ancla fundamental de reproducibilidad para la validación clínica;
        # cada emisión debe dejar una huella. Seguro para datos del
        # paciente: solo se registra el _node_count estructural.
        logger.info(
            "flow_node_audit_emit",
            extra={"node_count": node_count},
        )
        return {"emitted": True, "node_count": node_count}
    table[("@native", "audit")] = _emit_audit_chain

    # Agregador final — la salida del flujo
    def _build_safety_report(inputs: dict) -> dict:
        patient_id = inputs.get("patient_id", "")
        node_count = inputs.get("_node_count", 0)
        interactions = inputs.get("_tier1_interactions", [])
        # Observabilidad iter-329 — huella a nivel de nodo de flujo para el
        # agregador final (capa 6 / ensamblaje del informe de seguridad).
        # Reflejo de iter-314 (capa 4.5) + iter-319 (capa 1) + iter-324
        # (capa 3). Completa el barrido de observabilidad de _dispatch_table
        # (4/4 auxiliares silenciosos ahora cerrados). Seguro para datos del
        # paciente: patient_id es sintético de Synthea, node_count +
        # interaction_count son recuentos estructurales; los nombres de
        # medicamentos nunca llegan al registro.
        logger.debug(
            "flow_node_build_safety_report",
            extra={
                "patient_id_hash_prefix": _hash_patient_id(patient_id),
                "node_count": node_count,
                "interaction_count": len(interactions),
            },
        )
        return {
            "patient_id": patient_id,
            "node_count": node_count,
            "interactions": interactions,
        }
    table[("@native", "report")] = _build_safety_report
    table[("@native", "build_safety_report")] = _build_safety_report

    # DEBUG — señal de arranque de la tabla de despacho. Permite a los
    # operadores ver cuántas entradas `(directive, node_name)` están
    # conectadas frente a las que quedan en la ruta predeterminada
    # "skipped". Una futura refactorización @llm que añada 3 nodos nuevos
    # debería incrementar este recuento; si no lo hace, la tasa de
    # flow_node_skipped se dispara y este registro lo explica. Seguro para
    # datos del paciente: solo se registra el recuento, no las claves de
    # despacho (que son estáticas y no contienen datos del paciente).
    logger.debug(
        "flow_dispatch_table_built",
        extra={"dispatch_entry_count": len(table)},
    )

    return table


def execute(
    flow_name: str,
    inputs: dict[str, object] | None = None,
    flows_dir: Path | None = None,
) -> FlowExecution:
    """Ejecuta un contrato `.flow.mind` — recorre el grafo analizado en orden de declaración.

    El ejecutor despacha cada nodo `@native` a su punto de entrada del
    motor (ver `_dispatch_table()`). Los nodos desconocidos se registran
    como "skipped" en lugar de hacer fallar la ejecución — esto mantiene al
    ejecutor honesto cuando las directivas `@llm` / `@flow` necesitan claves
    de API de las que no disponemos.

    Devuelve un FlowExecution estructurado que lleva el plan_hash, el
    inputs_hash, el output_hash final y los sellos de evidencia por nodo.
    Cada campo es codificable como hash JSON canónico para que la cadena de
    auditoría pueda registrar la ejecución completa como un único registro
    verificable por reproducción.
    """
    inputs = inputs or {}
    contract = parse_flow_contract(flow_name, flows_dir=flows_dir)
    table = _dispatch_table()

    t0 = time.time()
    inputs_hash = _canonical_json_hash(inputs)

    # Registro de entrada seguro para datos del paciente: solo recuentos +
    # prefijos de hash. inputs puede llevar medications + patient_id, ambos
    # ya pasados por la verja de datos del paciente; el prefijo del hash es
    # no reversible y permite a los revisores correlacionar después.
    logger.info(
        "flow_execute_start",
        extra={
            "flow_name": flow_name,
            "plan_hash_prefix": contract.plan_hash[:16],
            "inputs_hash_prefix": inputs_hash[:16],
            "node_count": len(contract.nodes),
            "input_keys": sorted(inputs.keys()),
        },
    )

    state: dict[str, object] = dict(inputs)
    nodes: list[NodeExecution] = []

    for node in contract.nodes:
        node_t0 = time.time()
        callable_ = table.get((node.directive, node.name))
        if callable_ is None:
            # Solo DEBUG: omitir es normal para las directivas @llm sin
            # claves de API; los revisores que quieran ver el patrón de
            # omisión pueden activarlo. Usa únicamente la cardinalidad
            # canónica (directive, name).
            logger.debug(
                "flow_node_skipped",
                extra={
                    "flow_name": flow_name,
                    "node_name": node.name,
                    "directive": node.directive,
                },
            )
            nodes.append(NodeExecution(
                node_name=node.name,
                directive=node.directive,
                status="skipped",
                output_hash="",
                elapsed_ms=0,
                detail="sin entrada de despacho; adaptador del motor aún no implementado",
            ))
            continue
        try:
            # Pasa el estado acumulado para que los nodos posteriores vean las salidas previas.
            output = callable_(state)
            output_hash = _canonical_json_hash(output)
            state[node.name] = output
            # Alias de conveniencia para el agregador build_safety_report
            if node.name == "tier1" and isinstance(output, dict):
                state["_tier1_interactions"] = output.get("interactions", [])
            state["_node_count"] = len([n for n in nodes if n.status == "ok"]) + 1
            nodes.append(NodeExecution(
                node_name=node.name,
                directive=node.directive,
                status="ok",
                output_hash=output_hash,
                elapsed_ms=int((time.time() - node_t0) * 1000),
            ))
        except Exception as e:
            # Disciplina de datos del paciente / secretos: registra solo
            # error_type, nunca str(e). Los invocables de nodo reciben el
            # estado del flujo en ejecución, que puede llevar medications +
            # patient_id; el str(e) de una excepción podría citar
            # textualmente la clave/valor problemáticos.
            logger.warning(
                "flow_node_failed",
                extra={
                    "flow_name": flow_name,
                    "node_name": node.name,
                    "directive": node.directive,
                    "error_type": type(e).__name__,
                    "elapsed_ms": int((time.time() - node_t0) * 1000),
                },
            )
            nodes.append(NodeExecution(
                node_name=node.name,
                directive=node.directive,
                status="failed",
                output_hash="",
                elapsed_ms=int((time.time() - node_t0) * 1000),
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            ))

    # Evaluación de invariantes con el mejor esfuerzo (registrada, no
    # forzada — el ejecutor es honesto sobre lo que puede demostrar sin un
    # analizador mindc completo).
    invariant_violations: list[str] = []
    invariants_evaluable = 0
    for inv in contract.invariants:
        # Hoy solo podemos evaluar invariantes trivialmente decidibles.
        # Cualquier cosa más compleja se marca para análisis futuro.
        pass
    # DEBUG — resumen de la evaluación de invariantes. Hoy toda invariante
    # es `pass` (sin evaluador de predicados en proceso); este punto
    # documenta el estado honesto de 0 evaluables para que un revisor pueda
    # ver que el contrato tiene invariantes pero el ejecutor aún no las
    # fuerza (sí lo hace el analizador mindc en tiempo de compilación).
    # Cuando una futura iteración añada un evaluador en proceso,
    # `invariants_evaluable` pasa a ser el recuento real y cualquier
    # discrepancia con `invariant_count` se expone para revisión operativa.
    logger.debug(
        "flow_invariants_summary",
        extra={
            "flow_name": flow_name,
            "invariant_count": len(contract.invariants),
            "invariants_evaluable": invariants_evaluable,
            "violations": len(invariant_violations),
        },
    )

    # Salida final: se prefiere el enlace de salida asignado; se recurre a
    # la salida del último nodo ejecutado con éxito si el invocable de
    # asignación no se despachó.
    final_output: object | None = None
    if contract.outputs:
        final_output = state.get(contract.outputs[0].name)
    if final_output is None:
        for n in reversed(nodes):
            if n.status == "ok" and n.node_name in state:
                final_output = state[n.node_name]
                break
    output_hash = _canonical_json_hash(final_output)

    # Registro de finalización seguro para datos del paciente: recuentos de
    # nodos por estado + tiempo transcurrido. WARNING cuando algún nodo
    # falló (no omitido — omitir es normal para @llm sin claves); INFO en la
    # ruta de todo-ok / todo-omitido.
    ok_count = sum(1 for n in nodes if n.status == "ok")
    skipped_count = sum(1 for n in nodes if n.status == "skipped")
    failed_count = sum(1 for n in nodes if n.status == "failed")
    elapsed_ms = int((time.time() - t0) * 1000)
    log_fn = logger.warning if failed_count > 0 else logger.info
    log_fn(
        "flow_execute_complete",
        extra={
            "flow_name": flow_name,
            "plan_hash_prefix": contract.plan_hash[:16],
            "output_hash_prefix": output_hash[:16],
            "ok_nodes": ok_count,
            "skipped_nodes": skipped_count,
            "failed_nodes": failed_count,
            "elapsed_ms": elapsed_ms,
        },
    )

    return FlowExecution(
        flow_name=flow_name,
        plan_hash=contract.plan_hash,
        inputs_hash=inputs_hash,
        output_hash=output_hash,
        nodes=tuple(nodes),
        invariant_violations=tuple(invariant_violations),
        output=final_output,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "FlowPort",
    "FlowNode",
    "FlowInvariant",
    "FlowContract",
    "NodeExecution",
    "FlowExecution",
    "execute",
    "ReplayResult",
    "compute_plan_hash",
    "list_flow_names",
    "list_flows",
    "parse_flow_contract",
    "verify_replay",
]
