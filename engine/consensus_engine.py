"""Verificación por consenso multi-LLM — verificación independiente de hallazgos clínicos.

En lugar de confiar en un único LLM, este módulo ejecuta hasta 5 LLM de forma independiente
sobre el mismo hallazgo clínico y calcula una puntuación de concordancia. Solo se notifican
como verificados los hallazgos con una concordancia ≥2/3.

Modelos (utilizados cuando hay claves de API disponibles):
- OpenAI GPT-5.5 (validación clínica)
- Google Gemini 3.1 Pro (razonamiento de referencia, contexto de 1M)
- xAI Grok 4.3 (razonamiento rápido, contexto de 2M)
- Anthropic Claude Opus 4.8 (razonamiento profundo, orientado a la seguridad)
- Perplexity Sonar Reasoning Pro (búsqueda clínica fundamentada en la web)

La diversidad de arquitecturas de modelos reduce el riesgo de alucinaciones correlacionadas.
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VERIFICATION_PROMPT = """Eres un farmacéutico clínico que verifica un hallazgo de seguridad.

HALLAZGO CLÍNICO:
{finding}

EVIDENCIA DE APOYO:
{evidence}

CONTEXTO DEL PACIENTE:
{patient_context}

A partir de la evidencia proporcionada, ¿está de acuerdo en que se trata de un problema
de seguridad clínica real?
Responda ÚNICAMENTE con JSON válido:
{{"agrees": true/false, "confidence": 0.0-1.0, "reasoning": "explicación de 1-2 frases"}}"""


@dataclass(frozen=True)
class LLMVerdict:
    """Veredicto de un único LLM sobre un hallazgo clínico."""

    model: str
    agrees: bool
    confidence: float
    reasoning: str


@dataclass(frozen=True)
class ConsensusResult:
    """Resultado de la verificación por consenso multi-LLM."""

    finding: str
    verdicts: tuple[LLMVerdict, ...]
    agreement_count: int
    total_models: int
    consensus_level: str  # HIGH, MEDIUM, LOW, NONE, LIMITED
    confidence_score: float
    should_report: bool
    reasoning_summary: str


def _build_prompt(
    finding: str,
    evidence: list[dict[str, Any]],
    patient_context: dict[str, Any],
) -> str:
    """Construye el prompt de verificación."""
    evidence_text = "\n".join(
        f"- [{e.get('block_id', '?')}] {e.get('title', '')}: {e.get('content', '')}"
        for e in evidence[:10]
    )
    ctx_text = json.dumps(
        {
            k: v
            for k, v in patient_context.items()
            if k in ("medications", "conditions", "allergies", "patient_id")
        },
        default=str,
    )[:500]

    return _VERIFICATION_PROMPT.format(
        finding=finding,
        evidence=evidence_text or "No se proporcionaron bloques de evidencia.",
        patient_context=ctx_text,
    )


def _parse_verdict(text: str, model: str) -> LLMVerdict:
    """Analiza una respuesta de LLM y la convierte en un veredicto."""
    text = text.strip()
    # Gestiona los bloques de código markdown
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()

    try:
        data = json.loads(text)
        return LLMVerdict(
            model=model,
            agrees=bool(data.get("agrees", False)),
            confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
            reasoning=str(data.get("reasoning", ""))[:300],
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        # Si no se puede analizar el JSON, se buscan palabras clave de
        # concordancia. Se registra en nivel DEBUG para que los operadores
        # puedan auditar con qué frecuencia la salida de cada proveedor no es
        # JSON sin que el texto en bruto se filtre a los registros INFO de
        # producción. Disciplina de datos del paciente / secretos: solo
        # model + parse_error_type + response_length — NUNCA el cuerpo de la
        # respuesta (el LLM puede citar el prompt textualmente, incluidos los
        # nombres de fármacos que atravesaron la capa de redacción).
        logger.debug(
            "consensus_verdict_unparseable",
            extra={
                "model": model,
                "parse_error_type": type(e).__name__,
                "response_length": len(text),
            },
        )
        lower = text.lower()
        agrees = any(w in lower for w in ["agree", "yes", "genuine", "real concern", "confirmed"])
        return LLMVerdict(
            model=model,
            agrees=agrees,
            confidence=0.5,
            reasoning=text[:300],
        )


# --- Llamadas a los proveedores (cada una aislada, todas asíncronas) ---

_SYSTEM_MSG = "Eres un asistente de verificación de seguridad clínica. Responde ÚNICAMENTE con JSON."


async def _call_openai(prompt: str, api_key: str) -> LLMVerdict:
    """Llama a OpenAI GPT-5.5 para la verificación."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-5.5",
                "messages": [
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_completion_tokens": 256,
            },
        )
        if resp.status_code != 200:
            # WARNING estructurado previo al lanzamiento — solo provider +
            # status_code (sin cuerpo, que puede incluir mensajes de límite
            # de tasa que citan de vuelta el prompt). El manejador posterior
            # consensus_provider_error solo ve el tipo RuntimeError, que pierde
            # el estado HTTP. Este evento lo conserva para la reproducción de
            # la auditoría.
            logger.warning(
                "consensus_provider_http_error",
                extra={
                    "provider": "OpenAI-GPT-5.5",
                    "status_code": resp.status_code,
                },
            )
            raise RuntimeError(f"OpenAI devolvió {resp.status_code}")
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_verdict(text, "OpenAI-GPT-5.5")


async def _call_google(prompt: str, api_key: str, model_id: str, label: str) -> LLMVerdict:
    """Llama a un modelo de Google para la verificación."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
            headers={"x-goog-api-key": api_key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 256},
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "consensus_provider_http_error",
                extra={
                    "provider": label,
                    "status_code": resp.status_code,
                },
            )
            raise RuntimeError(f"{label} devolvió {resp.status_code}")
        candidates = resp.json().get("candidates", [])
        if not candidates:
            # Google devuelve 200 con candidatos vacíos cuando el prompt es
            # filtrado por los clasificadores de seguridad de Gemini (p. ej.,
            # contenido clínico marcado como consejo médico). Es un modo de
            # fallo distinto de una respuesta que no es 200 — se registra por
            # separado para que los operadores puedan distinguir las caídas de
            # la API de los falsos positivos del filtro de seguridad. Solo la
            # etiqueta del proveedor; nunca el prompt ni el cuerpo de la
            # respuesta (Google devuelve el prompt en algunas rutas de error
            # y redactamos los datos del paciente aguas arriba, pero por
            # defensa en profundidad).
            logger.warning(
                "consensus_provider_no_candidates",
                extra={
                    "provider": label,
                    "status_code": resp.status_code,
                    "reason": "empty_candidates_likely_safety_filter",
                },
            )
            raise RuntimeError(f"{label} no devolvió candidatos")
        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return _parse_verdict(text, label)


async def _call_openai_compatible(
    prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    label: str,
) -> LLMVerdict:
    """Llama a cualquier API compatible con OpenAI (xAI, Perplexity)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 256,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "consensus_provider_http_error",
                extra={
                    "provider": label,
                    "status_code": resp.status_code,
                },
            )
            raise RuntimeError(f"{label} devolvió {resp.status_code}")
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_verdict(text, label)


async def _call_perplexity(prompt: str, api_key: str) -> LLMVerdict:
    """Llama a Perplexity Sonar Reasoning Pro para la verificación."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "sonar-reasoning-pro",
                "messages": [
                    {"role": "system", "content": _SYSTEM_MSG},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 256,
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "consensus_provider_http_error",
                extra={
                    "provider": "Perplexity-Sonar-Pro",
                    "status_code": resp.status_code,
                },
            )
            raise RuntimeError(f"Perplexity devolvió {resp.status_code}")
        text = resp.json()["choices"][0]["message"]["content"]
        return _parse_verdict(text, "Perplexity-Sonar-Pro")


async def _call_anthropic(prompt: str, api_key: str) -> LLMVerdict:
    """Llama a Anthropic Claude Opus 4.8 para la verificación."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-opus-4-8",
                "max_tokens": 256,
                "temperature": 0.1,
                "system": _SYSTEM_MSG,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "consensus_provider_http_error",
                extra={
                    "provider": "Anthropic-Claude-Opus-4.7",
                    "status_code": resp.status_code,
                },
            )
            raise RuntimeError(f"Anthropic devolvió {resp.status_code}")
        content = resp.json().get("content", [])
        text = content[0].get("text", "") if content else ""
        return _parse_verdict(text, "Anthropic-Claude-Opus-4.7")


async def verify_finding_consensus(
    finding: str,
    evidence: list[dict[str, Any]],
    patient_context: dict[str, Any],
) -> ConsensusResult:
    """Ejecuta la verificación por consenso multi-LLM sobre un hallazgo clínico.

    Lanza en paralelo todos los LLM disponibles (hasta 5). Calcula la puntuación de
    concordancia. ≥90% = HIGH, ≥67% = MEDIUM, ≥1 = LOW, 0 = NONE.
    Los hallazgos con menos de 2 modelos disponibles reciben consensus_level "LIMITED".

    Args:
        finding: El hallazgo clínico a verificar.
        evidence: Bloques de evidencia de apoyo de la memoria del paciente.
        patient_context: Dict de resumen del paciente.

    Returns:
        ConsensusResult inmutable con los veredictos y el nivel de concordancia.
    """
    # Protección de los datos del paciente
    try:
        from engine.phi_detector import redact_phi
        prompt, _ = redact_phi(_build_prompt(finding, evidence, patient_context))
    except ImportError as exc:
        # Nivel WARNING — la ausencia de la protección de datos del paciente es
        # operativamente significativa. La construcción posterior del prompt
        # continúa sin redacción, lo cual es aceptable cuando phi_detector no
        # está disponible (p. ej., entornos de desarrollo/pruebas), pero los
        # operadores deben ver la ausencia en los registros de producción. Solo
        # error_type — sin el cuerpo de la excepción, que puede incluir rutas
        # del sistema de ficheros.
        logger.warning(
            "consensus_phi_guard_unavailable",
            extra={
                "error_type": type(exc).__name__,
                "fallback": "prompt_constructed_without_redaction",
            },
        )
        prompt = _build_prompt(finding, evidence, patient_context)

    openai_key = os.environ.get("OPENAI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    xai_key = os.environ.get("XAI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY")
    nvidia_key = os.environ.get("NVIDIA_API_KEY")

    # Registro de entrada que protege los datos del paciente: solo recuentos —
    # sin el texto del hallazgo, sin el cuerpo de la evidencia, sin el contexto
    # del paciente. La disponibilidad de proveedores es configuración pública.
    available_providers = [
        label for key, label in (
            (openai_key, "OpenAI-GPT-5.5"),
            (google_key, "Gemini-3.1-Pro"),
            (xai_key, "xAI-Grok-4.3"),
            (anthropic_key, "Anthropic-Claude-Opus-4.7"),
            (perplexity_key, "Perplexity-Sonar-Pro"),
            (nvidia_key, "NVIDIA-Nemotron-Ultra-253B"),
        )
        if key
    ]
    logger.debug(
        "consensus_dispatch",
        extra={
            "providers_available": len(available_providers),
            "providers": available_providers,
            "evidence_block_count": len(evidence),
            "prompt_length": len(prompt),
        },
    )

    tasks: list[tuple[str, Any]] = []

    # Nivel 1: Modelos con validación clínica
    if openai_key:
        tasks.append(("OpenAI-GPT-5.5", _call_openai(prompt, openai_key)))
    if google_key:
        tasks.append(("Gemini-3.1-Pro", _call_google(
            prompt, google_key, "gemini-3.1-pro-preview", "Gemini-3.1-Pro",
        )))

    # Nivel 2: Modelos diversos (reducen los errores correlacionados)
    if xai_key:
        tasks.append(("xAI-Grok-4.3", _call_openai_compatible(
            prompt, xai_key, "https://api.x.ai", "grok-4.3", "xAI-Grok-4.3",
        )))
    if anthropic_key:
        tasks.append(("Anthropic-Claude-Opus-4.7", _call_anthropic(prompt, anthropic_key)))
    if perplexity_key:
        tasks.append(("Perplexity-Sonar-Pro", _call_perplexity(
            prompt, perplexity_key,
        )))
    if nvidia_key:
        tasks.append(("NVIDIA-Nemotron-Ultra-253B", _call_openai_compatible(
            prompt,
            nvidia_key,
            "https://integrate.api.nvidia.com",
            "nvidia/llama-3.1-nemotron-ultra-253b-v1",
            "NVIDIA-Nemotron-Ultra-253B",
        )))

    if not tasks:
        # Nivel WARNING — los operadores deben ver cuándo el consenso no puede
        # ejecutarse en absoluto. El control de abstención posterior lo
        # detectará, pero la señal debe aflorar en los registros de producción.
        logger.warning(
            "consensus_no_providers",
            extra={
                "providers_available": 0,
                "level": "NONE",
                "should_report": False,
            },
        )
        return ConsensusResult(
            finding=finding,
            verdicts=(),
            agreement_count=0,
            total_models=0,
            consensus_level="NONE",
            confidence_score=0.0,
            should_report=False,
            reasoning_summary="No hay claves de API de LLM disponibles para la verificación por consenso.",
        )

    # Lanza todas en paralelo
    verdicts: list[LLMVerdict] = []
    results = await asyncio.gather(
        *(t[1] for t in tasks), return_exceptions=True
    )

    for (label, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            # Error por proveedor — solo el tipo (no el cuerpo del mensaje, que
            # puede filtrar cuerpos de petición/respuesta en SDK de terceros).
            logger.warning(
                "consensus_provider_error",
                extra={
                    "provider": label,
                    "error_type": type(result).__name__,
                },
            )
            verdicts.append(
                LLMVerdict(
                    model=label,
                    agrees=False,
                    confidence=0.0,
                    reasoning=f"Falló la llamada a la API: {result}",
                )
            )
        else:
            # Éxito por proveedor — solo el booleano del veredicto + el rango de
            # confianza, nunca la cadena de razonamiento (la salida del modelo
            # puede citar datos del paciente aunque el prompt se haya redactado).
            logger.debug(
                "consensus_provider_verdict",
                extra={
                    "provider": label,
                    "agrees": result.agrees,
                    "confidence_bucket": (
                        "high" if result.confidence >= 0.75
                        else "medium" if result.confidence >= 0.5
                        else "low"
                    ),
                },
            )
            verdicts.append(result)

    agreement_count = sum(1 for v in verdicts if v.agrees)
    total = len(verdicts)

    if total < 2:
        level = "LIMITED"
    elif agreement_count == total:
        level = "HIGH"
    elif agreement_count >= total * 2 / 3:
        level = "MEDIUM"
    elif agreement_count >= 1:
        level = "LOW"
    else:
        level = "NONE"

    # Confianza ponderada: los modelos que concuerdan con mayor confianza pesan más
    if total > 0:
        weighted_sum = sum(
            v.confidence if v.agrees else (1.0 - v.confidence)
            for v in verdicts
        )
        confidence = weighted_sum / total
    else:  # pragma: no cover — el retorno anticipado gestiona las tareas vacías
        confidence = 0.0

    reasoning_parts = [
        f"{v.model}: {'de acuerdo' if v.agrees else 'en desacuerdo'} ({v.confidence:.1f}) — {v.reasoning}"
        for v in verdicts
    ]

    # Registro del resultado agregado: solo el nivel categórico + recuentos. INFO
    # cuando el hallazgo se vaya a notificar; WARNING cuando el consenso baje a
    # LOW / NONE / LIMITED — esos son los disparadores de abstención posteriores.
    should_report = level in ("HIGH", "MEDIUM")
    log_fn = logger.info if should_report else logger.warning
    log_fn(
        "consensus_aggregated",
        extra={
            "level": level,
            "agreement_count": agreement_count,
            "total_models": total,
            "should_report": should_report,
            "confidence_score": round(confidence, 2),
        },
    )

    return ConsensusResult(
        finding=finding,
        verdicts=tuple(verdicts),
        agreement_count=agreement_count,
        total_models=total,
        consensus_level=level,
        confidence_score=round(confidence, 2),
        should_report=should_report,
        reasoning_summary=" | ".join(reasoning_parts),
    )


def verify_finding_consensus_sync(
    finding: str,
    evidence: list[dict[str, Any]],
    patient_context: dict[str, Any],
) -> ConsensusResult:
    """Envoltorio síncrono para verify_finding_consensus."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Ya estamos en un contexto asíncrono — se crea un hilo nuevo
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                verify_finding_consensus(finding, evidence, patient_context),
            )
            return future.result(timeout=30)
    else:
        return asyncio.run(
            verify_finding_consensus(finding, evidence, patient_context)
        )
