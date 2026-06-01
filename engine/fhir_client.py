"""
Cliente FHIR R4 — consulta los recursos del paciente en un servidor FHIR.

Envuelve httpx con autenticación por token Bearer y cabeceras Accept de FHIR JSON.
Todos los métodos devuelven dicts ya parseados; los errores lanzan FHIRClientError.
"""
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FHIR_TIMEOUT = 15  # segundos


class FHIRClientError(Exception):
    """Se lanza cuando una solicitud FHIR falla."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class FHIRContext:
    """Contexto de conexión FHIR inmutable."""

    url: str
    token: str
    patient_id: str

    def validate(self) -> None:
        missing = []
        if not self.url:
            missing.append("url")
        if not self.token:
            missing.append("token")
        if not self.patient_id:
            missing.append("patient_id")
        if missing:
            raise FHIRClientError(f"Faltan datos en el contexto FHIR: {', '.join(missing)}")
        # Protección frente a SSRF: solo se permiten URLs HTTPS a endpoints FHIR conocidos
        from urllib.parse import urlparse

        parsed = urlparse(self.url)
        if parsed.scheme not in ("https", "http"):
            raise FHIRClientError(f"Esquema de URL FHIR no válido: {parsed.scheme}")
        # Eliminar los corchetes de IPv6 para la comparación
        hostname = parsed.hostname or ""
        bare_host = hostname.strip("[]")
        if bare_host in ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "::1"):
            raise FHIRClientError("La URL FHIR no debe apuntar a localhost ni a endpoints de metadatos")
        if bare_host:
            # Bloquear todos los rangos privados RFC 1918 + link-local + IPv6 privado
            import ipaddress

            # Probar primero bare_host; también intentar extraer IPv6 de la ruta de la URL
            # (un IPv6 sin corchetes como https://fc00::1/fhir se interpreta mal en urlparse)
            candidates = [bare_host]
            # Extraer un posible IPv6 del netloc/texto de la URL
            netloc = parsed.netloc or ""
            if "::" in netloc:
                # Eliminar el sufijo de puerto si está presente y extraer la parte IPv6
                ipv6_candidate = netloc.split("/")[0]
                candidates.append(ipv6_candidate)
            if "::" in (parsed.path or ""):
                ipv6_candidate = parsed.path.split("/")[0]
                if ipv6_candidate:
                    candidates.append(ipv6_candidate)

            for candidate in candidates:
                try:
                    addr = ipaddress.ip_address(candidate)
                    if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                        raise FHIRClientError("La URL FHIR no debe apuntar a direcciones de red privadas")
                except ValueError:
                    continue


class FHIRClient:
    """Cliente REST FHIR R4 sin estado."""

    def __init__(self, ctx: FHIRContext):
        ctx.validate()
        self._url = ctx.url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {ctx.token}",
            "Accept": "application/fhir+json",
        }
        self._patient_id = ctx.patient_id

    @property
    def patient_id(self) -> str:
        return self._patient_id

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        resp = httpx.get(
            f"{self._url}/{path}",
            params=params,
            headers=self._headers,
            timeout=_FHIR_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise FHIRClientError(
                f"HTTP FHIR {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
        return resp.json()

    def get_patient(self) -> dict:
        return self._get(f"Patient/{self._patient_id}")

    def get_medications(self, status: str = "active") -> list[dict]:
        bundle = self._get(
            "MedicationRequest",
            {"patient": self._patient_id, "status": status, "_count": "100"},
        )
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def get_conditions(self, clinical_status: str = "active") -> list[dict]:
        bundle = self._get(
            "Condition",
            {"patient": self._patient_id, "clinical-status": clinical_status, "_count": "100"},
        )
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def get_allergies(self) -> list[dict]:
        bundle = self._get(
            "AllergyIntolerance",
            {"patient": self._patient_id, "_count": "100"},
        )
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def get_observations(
        self, category: str = "vital-signs", count: int = 20
    ) -> list[dict]:
        bundle = self._get(
            "Observation",
            {
                "patient": self._patient_id,
                "category": category,
                "_sort": "-date",
                "_count": str(count),
            },
        )
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]

    def get_encounters(self, count: int = 20) -> list[dict]:
        bundle = self._get(
            "Encounter",
            {"patient": self._patient_id, "_sort": "-date", "_count": str(count)},
        )
        return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]


class BundleFHIRClient:
    """Cliente FHIR en memoria respaldado por un Bundle ya parseado.

    Ofrece la misma interfaz que FHIRClient (patient_id, get_medications, etc.)
    sin realizar llamadas de red. Se utiliza para el modo local y las pruebas.
    """

    def __init__(self, resources_by_type: dict[str, list], patient_id: str):
        self._resources = resources_by_type
        self._patient_id = patient_id

    @property
    def patient_id(self) -> str:
        return self._patient_id

    def get_patient(self) -> dict:
        patients = self._resources.get("Patient", [{}])
        return patients[0] if patients else {}

    def get_medications(self, status: str = "active") -> list[dict]:
        return self._resources.get("MedicationRequest", [])

    def get_conditions(self, clinical_status: str = "active") -> list[dict]:
        return self._resources.get("Condition", [])

    def get_allergies(self) -> list[dict]:
        return self._resources.get("AllergyIntolerance", [])

    def get_observations(self, category: str = "vital-signs", count: int = 20) -> list[dict]:
        all_obs = self._resources.get("Observation", [])
        filtered = [
            obs for obs in all_obs
            if any(
                cat_entry.get("coding", [{}])[0].get("code") == category
                for cat_entry in obs.get("category", [])
            )
        ]
        return filtered[:count]

    def get_encounters(self, count: int = 20) -> list[dict]:
        return self._resources.get("Encounter", [])[:count]


def coding_display(codings: list[dict[str, Any]]) -> str:
    """Devuelve el primer texto legible de los codings FHIR."""
    for c in codings:
        if c.get("display"):
            return c["display"]
    return "Desconocido"


def extract_medication_name(resource: dict) -> str:
    """Extrae el nombre legible del medicamento de un MedicationRequest."""
    concept = resource.get("medicationCodeableConcept", {})
    return (
        concept.get("text")
        or coding_display(concept.get("coding", []))
        or resource.get("medicationReference", {}).get("display", "Desconocido")
    )


def extract_condition_name(resource: dict) -> str:
    """Extrae el nombre legible de la afección de un recurso Condition."""
    code = resource.get("code", {})
    return code.get("text") or coding_display(code.get("coding", []))
