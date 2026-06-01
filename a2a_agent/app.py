"""
Agente Historia Clínica — punto de entrada de la aplicación A2A.

Inicia el servidor con:
    uvicorn a2a_agent.app:a2a_app --host 0.0.0.0 --port 8001

La tarjeta del agente se publica de forma pública en:
    GET http://localhost:8001/.well-known/agent-card.json
"""
import json
import logging
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentSkill,
    APIKeySecurityScheme,
    In,
    SecurityScheme,
)
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a_agent.agent import root_agent

logger = logging.getLogger(__name__)

# ── Middleware de clave de API ──────────────────────────────────────────────

_raw_keys = os.getenv("API_KEYS", "")
if not _raw_keys:
    logger.warning("API_KEYS no está configurada — se rechazan todas las solicitudes hasta configurarla")
VALID_API_KEYS = set(filter(None, _raw_keys.split(",")))


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limitador de tasa sencillo con ventana deslizante (por clave de API, 60 sol./min)."""

    MAX_REQUESTS = 60
    WINDOW_SECONDS = 60

    def __init__(self, app):
        super().__init__(app)
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/.well-known/agent-card.json":
            return await call_next(request)
        key = request.headers.get("X-API-Key", request.client.host if request.client else "unknown")
        now = time.monotonic()
        cutoff = now - self.WINDOW_SECONDS
        window = [t for t in self._requests[key] if t > cutoff]
        if len(window) >= self.MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"error": "Demasiadas solicitudes", "detail": "Se superó el límite de tasa (60/min)"},
            )
        window.append(now)
        self._requests[key] = window
        return await call_next(request)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/.well-known/agent-card.json":
            return await call_next(request)
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "No autorizado", "detail": "Se requiere la cabecera X-API-Key"},
            )
        if api_key not in VALID_API_KEYS:
            return JSONResponse(
                status_code=403,
                content={"error": "Prohibido", "detail": "Clave de API no válida"},
            )
        return await call_next(request)


# ── Tarjeta del agente ──────────────────────────────────────────────────────

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001")
PO_PLATFORM_URL = os.getenv("PO_PLATFORM_BASE_URL", "http://localhost:5139")
PORT = int(os.getenv("PORT", "8001"))

agent_card = AgentCard(
    name="hcli_agent",
    description=(
        "Historia Clínica — Memoria clínica persistente, auditable y resistente a "
        "contradicciones. Ofrece análisis de seguridad de medicación, recuperación de "
        "contexto clínico con control de confianza y registros de auditoría a prueba de "
        "manipulaciones."
    ),
    url=BASE_URL,
    version="0.1.0",
    defaultInputModes=["text/plain"],
    defaultOutputModes=["text/plain"],
    capabilities=AgentCapabilities(
        streaming=True,
        pushNotifications=False,
        stateTransitionHistory=True,
        extensions=[
            AgentExtension(
                uri=f"{PO_PLATFORM_URL}/schemas/a2a/v1/fhir-context",
                description="Contexto FHIR R4 — proporciona la identidad del paciente y las credenciales del servidor FHIR.",
                required=True,
            )
        ],
    ),
    skills=[
        AgentSkill(
            id="medication-safety-review",
            name="medication-safety-review",
            description=(
                "Conciliación integral de la medicación: detección de interacciones "
                "farmacológicas, referencia cruzada de alergias y puntuación de gravedad. "
                "Usa núcleos de puntuación clínica."
            ),
            tags=["medications", "safety", "interactions", "allergies"],
        ),
        AgentSkill(
            id="clinical-context-recall",
            name="clinical-context-recall",
            description=(
                "Responde preguntas sobre la historia del paciente usando memoria persistente "
                "con recuperación ponderada por importancia y control de confianza (abstención "
                "cuando la evidencia es insuficiente)."
            ),
            tags=["memory", "recall", "context", "history"],
        ),
        AgentSkill(
            id="contradiction-assessment",
            name="contradiction-assessment",
            description=(
                "Examina el registro del paciente en busca de información contradictoria entre "
                "profesionales, fechas y fuentes de datos. Detecta conflictos entre alergias y "
                "medicación e interacciones farmacológicas peligrosas."
            ),
            tags=["contradictions", "safety", "audit"],
        ),
        AgentSkill(
            id="care-transition-summary",
            name="care-transition-summary",
            description=(
                "Genera un resumen estructurado de traspaso para las transiciones asistenciales. "
                "Destaca los problemas activos, los conflictos de medicación y las acciones pendientes."
            ),
            tags=["transitions", "handoff", "summary"],
        ),
        AgentSkill(
            id="explain-conflict",
            name="explain-conflict",
            description=(
                "Explicación de conflictos clínicos impulsada por IA generativa. Usa un LLM para "
                "generar una justificación específica del paciente ante un conflicto de seguridad "
                "detectado, con citas de evidencia y abstención estricta cuando la evidencia es "
                "insuficiente."
            ),
            tags=["genai", "explanation", "safety", "citations"],
        ),
    ],
    securitySchemes={
        "apiKey": SecurityScheme(
            root=APIKeySecurityScheme(
                type="apiKey",
                name="X-API-Key",
                in_=In.header,
                description="Clave de API necesaria para acceder al agente Historia Clínica.",
            )
        )
    },
    security=[{"apiKey": []}],
)

# ── Construir la aplicación A2A ─────────────────────────────────────────────

a2a_app = to_a2a(root_agent, port=PORT, agent_card=agent_card)
a2a_app.add_middleware(ApiKeyMiddleware)
a2a_app.add_middleware(RateLimitMiddleware)
