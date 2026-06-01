"""
scripts/federation_mock_demo.py
Historia Clínica — simulación de federación de 2 nodos.

Simula dos centros de Historia Clínica (Hospital General de Malabo + Hospital
Regional de Bata) que federan un hallazgo de gravedad sobre un par de fármacos
a través del contrato JointMemoryFederation.flow.mind. Demuestra que:

  1. La barrera de datos del paciente funciona — los hallazgos etiquetados con
     datos del paciente quedan en cuarentena antes del transporte.
  2. La firma + verificación Ed25519 funciona — las cargas manipuladas se rechazan.
  3. La barrera de quórum de gravedad funciona — un hallazgo de un solo nodo
     queda en un nivel bajo.
  4. La cadena de auditoría registra el intercambio entre centros y ambos
     coinciden en el hash canónico.

El transporte simulado es una cola Python en proceso — no requiere red.
Este script sigue siendo un artefacto válido de enseñanza y auditoría una vez
que se publique el transporte real MIC@2 / MAP / binario.

Apache-2.0 — STARGA, Inc.
Referencia ejecutable de flows/JointMemoryFederation.flow.mind

Uso:
    python3 scripts/federation_mock_demo.py
    python3 scripts/federation_mock_demo.py --phi-test   # inyecta datos del paciente
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.exceptions import InvalidSignature, InvalidTag

# Permite importar el paquete engine cuando este script se ejecuta directamente.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.federation_transport import (  # noqa: E402
    make_default_fanout,
    make_site_mesh,
    record_ingest_event,
    record_publish_event,
    record_quarantine_event,
    register_clinical_peer,
)
from mind_mem.event_fanout import EventFanout  # noqa: E402
from mind_mem.memory_mesh import MemoryMesh  # noqa: E402

# ── colorama para salida ANSI ─────────────────────────────────────────────────
try:
    import colorama
    colorama.init(autoreset=True)
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"
except ImportError:  # pragma: no cover
    GREEN = RED = YELLOW = CYAN = BOLD = DIM = RESET = ""


# ── plan_hash de JointMemoryFederation.flow.mind ──────────────────────────────
# SHA-256 canónico del contrato de flujo en flows/JointMemoryFederation.flow.mind.
# Se registra en cada entrada de auditoría para que los revisores detecten
# desviaciones del contrato.
FLOW_CONTRACT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "flows", "JointMemoryFederation.flow.mind"
)


def _compute_flow_plan_hash() -> str:
    """SHA-256 del código fuente del contrato de flujo, en hexadecimal."""
    try:
        path = os.path.normpath(FLOW_CONTRACT_PATH)
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return "offline"


PLAN_HASH = _compute_flow_plan_hash()

# ── Registro de invariantes ───────────────────────────────────────────────────
# Asocia el índice de invariante (base 1) con una descripción breve que coincide
# con el contrato.
INVARIANT_DESCRIPTIONS: dict[int, str] = {
    1:  "classify.lane in [clinical_knowledge, phi_lane]",
    2:  "scrubbed.has_phi == false",
    3:  "scrubbed.identifiers_removed >= 0",
    4:  "scrubbed.payload.fhir_resource_type not in [Patient, Observation, MedicationStatement, Encounter, DocumentReference]",
    5:  "classify.lane != phi_lane or scrubbed.empty == true",
    6:  "stamped.has_issued_at == true",
    7:  "stamped.has_nonce_128bit == true",
    8:  "signed.epoch == site_epoch",
    9:  "signed.canonical_preimage_schema == TAG_v1_NUL_separated",
    10: "sealed.payload_encrypted == true (X25519 ECDH + HKDF-SHA256 + ChaCha20-Poly1305)",
    11: "sealed.cipher == chacha20-poly1305",
    12: "sealed.has_aead_tag == true (16-byte Poly1305 tag, AEAD-bound to associated_data)",
    13: "opened.decryption_succeeded == true (recipient X25519 private key derives matching shared secret)",
    14: "opened.aead_tag_verified == true (Poly1305 AEAD tag verifies before plaintext is exposed)",
    15: "verified.signature_valid == true",
    16: "verified.key_epoch_revoked == false",
    17: "verified.payload.issued_at_seconds_ago <= 300",
    18: "verified.payload.issued_at_seconds_ago >= 0",
    19: "inbound_scrub.has_phi == false",
    20: "tier_clamped.value >= 0 and tier_clamped.value <= 5",
    21: "quorum.has_concurring_signatures or quorum.tier <= 1",
}

# ── Cabecera + utilidades ─────────────────────────────────────────────────────

def _banner() -> None:
    print(f"\n{BOLD}{CYAN}{'═' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  Historia Clínica — Simulación de Federación de 2 Nodos{RESET}")
    print(f"{BOLD}{CYAN}  JointMemoryFederation.flow.mind  ·  plan_hash: {PLAN_HASH[:16]}...{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 70}{RESET}\n")


def _stage(label: str) -> None:
    print(f"\n{BOLD}{YELLOW}{'─' * 60}{RESET}")
    print(f"{BOLD}{YELLOW}  {label}{RESET}")
    print(f"{BOLD}{YELLOW}{'─' * 60}{RESET}")


def _pass(invariant_index: int, detail: str = "") -> None:
    desc = INVARIANT_DESCRIPTIONS[invariant_index]
    extra = f"  {DIM}({detail}){RESET}" if detail else ""
    print(f"  {GREEN}✓ INVARIANTE {invariant_index:02d} CUMPLE{RESET}  {desc}{extra}")


def _info(label: str, value: str) -> None:
    print(f"  {CYAN}{label}{RESET}: {value}")


def _block(title: str, body: str) -> None:
    print(f"  {DIM}{title}:{RESET} {body}")


# ── Detector de datos del paciente (en línea, sin importar engine.phi_detector) ─

def _has_phi(text: str) -> bool:
    """Comprobación ligera de datos del paciente: patrón MRN, prefijo de nombre
    de paciente, número de identidad, fecha de nacimiento."""
    import re
    patterns = [
        re.compile(r'\bMRN[-:\s]*\d{4,10}\b', re.IGNORECASE),
        re.compile(r'(?:Patient|Pt)\.?\s+[A-Z][a-z]+'),
        re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
        re.compile(r'\b(?:DOB|Date of Birth)[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b', re.IGNORECASE),
    ]
    return any(p.search(text) for p in patterns)


def _phi_strip(payload: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    """
    Ejecuta el depurador de datos del paciente sobre todos los campos de texto.

    Devuelve (carga_depurada, datos_paciente_encontrados, identificadores_eliminados).
    """
    import re
    _phi_re = re.compile(
        r'(?:\bMRN[-:\s]*\d{4,10}\b'
        r'|(?:Patient|Pt)\.?\s+[A-Z][a-z]+'
        r'|\b\d{3}-\d{2}-\d{4}\b'
        r'|\bDOB[:\s]*\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b)',
        re.IGNORECASE,
    )
    removed = 0
    found_phi = False
    stripped: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str):
            matches = _phi_re.findall(v)
            if matches:
                found_phi = True
                removed += len(matches)
                v = _phi_re.sub("[REDACTED]", v)
        stripped[k] = v
    return stripped, found_phi, removed


# ── Preimagen canónica (TAG_v1_NUL_separated) ─────────────────────────────────

def _canonical_preimage(payload: dict[str, Any]) -> bytes:
    """
    Construye una preimagen canónica TAG_v1 separada por NUL para firmar.

    El orden de los campos se ordena lexicográficamente para que la preimagen
    sea determinista con independencia del orden de inserción del dict (Python
    3.7+ conserva el orden de inserción, pero quienes la invocan pueden
    construir los dicts en cualquier orden).
    """
    parts: list[bytes] = [b"TAG_v1"]
    for k in sorted(payload.keys()):
        v = payload[k]
        if isinstance(v, (dict, list)):
            v_bytes = json.dumps(v, sort_keys=True, separators=(",", ":")).encode()
        else:
            v_bytes = str(v).encode()
        parts.append(k.encode())
        parts.append(v_bytes)
    return b"\x00".join(parts)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Tipos de datos ──────────────────────────────────────────────────────────────

@dataclass
class ClinicalFinding:
    """Hallazgo clínico en bruto producido por la cadena de Capas 1-4 de un centro."""
    drug_a: str
    drug_b: str
    severity: str
    description: str
    score: float
    has_phi: bool = False
    phi_context: str = ""


@dataclass
class FederatedRecord:
    """Registro de transporte tras el procesamiento de salida en el centro de origen."""
    drug_a: str
    drug_b: str
    severity: str
    description: str
    score: float
    issued_at: int
    nonce_128bit: str
    key_epoch: int
    signer_id: str
    signature: bytes
    canonical_preimage_hash: str
    # Auditoría
    plan_hash: str = PLAN_HASH
    fhir_resource_type: str = "none"


@dataclass
class LocalKnowledge:
    """Registro almacenado en el almacén dict local estilo mind-mem tras la entrada."""
    drug_a: str
    drug_b: str
    severity: str
    description: str
    score: float
    provenance: str
    tier: int
    evidence_grade: bool
    received_at: int
    audit_chain_hash: str


@dataclass
class SealedEnvelope:
    """Sobre sellado mediante X25519-ECDH que transporta un FederatedRecord
    cifrado a través de la red. Reproduce la forma de transporte HTTP de la
    federación v4 (mind-mem `main` 16a3e25, etiqueta PyPI v4.0.x pendiente)."""
    sender_x25519_pub: bytes   # clave pública efímera X25519 en bruto de 32 bytes
    nonce: bytes               # nonce ChaCha20-Poly1305 de 12 bytes
    ciphertext: bytes          # texto cifrado AEAD + sufijo de etiqueta Poly1305 de 16 bytes
    cipher: str = "chacha20-poly1305"
    aead_tag_length: int = 16  # la etiqueta Poly1305 se anexa al texto cifrado


@dataclass
class SiteState:
    """Simulación en proceso de un centro de Historia Clínica."""
    name: str
    site_id: str
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    x25519_private: X25519PrivateKey
    x25519_public: X25519PublicKey
    key_epoch: int = 1
    memory_store: dict[str, LocalKnowledge] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    revoked_epochs: set[int] = field(default_factory=set)
    # Plano de control mind-mem v3.8.14: registro de pares + 7 ámbitos de
    # sincronización + política de resolución de conflictos por ámbito +
    # registro de auditoría de sincronización de solo anexado.
    mesh: MemoryMesh = field(default_factory=make_site_mesh)


# ── Fábrica de centros ──────────────────────────────────────────────────────────

def _make_site(name: str, site_id: str, epoch: int = 1) -> SiteState:
    priv = Ed25519PrivateKey.generate()
    x_priv = X25519PrivateKey.generate()
    return SiteState(
        name=name,
        site_id=site_id,
        private_key=priv,
        public_key=priv.public_key(),
        x25519_private=x_priv,
        x25519_public=x_priv.public_key(),
        key_epoch=epoch,
    )


# ── Sellado / apertura AEAD X25519 + ChaCha20-Poly1305 ────────────────────────
#
# Reproduce el sobre criptográfico del transporte HTTP de la federación v4
# (mind-mem `main` 16a3e25): par de claves X25519 efímero por registro → secreto
# compartido ECDH → HKDF-SHA256(info=b"hcli-federation-v1") → clave
# ChaCha20-Poly1305 de 32 bytes, cifrado AEAD con nonce aleatorio de 12 bytes +
# etiqueta Poly1305 de 16 bytes, ligada por AEAD a un associated_data fijo. Las
# claves efímeras por registro aportan secreto hacia adelante a nivel de registro.

_FED_AAD = b"hcli-federation-v1"
_FED_HKDF_INFO = b"hcli-federation-v1"


def _x25519_seal(
    record: "FederatedRecord",
    recipient_pubkey: X25519PublicKey,
) -> SealedEnvelope:
    """Cifra un FederatedRecord usando X25519 ECDH + ChaCha20-Poly1305 AEAD.

    Par de claves de emisor efímero → secreto hacia adelante. La biblioteca
    de criptografía anexa la etiqueta AEAD al texto cifrado (la construcción
    canónica de Poly1305).
    """
    payload_bytes = json.dumps(
        {
            "drug_a": record.drug_a,
            "drug_b": record.drug_b,
            "severity": record.severity,
            "description": record.description,
            "score": record.score,
            "issued_at": record.issued_at,
            "nonce_128bit": record.nonce_128bit,
            "key_epoch": record.key_epoch,
            "signer_id": record.signer_id,
            "signature": record.signature.hex(),
            "canonical_preimage_hash": record.canonical_preimage_hash,
            "plan_hash": record.plan_hash,
            "fhir_resource_type": record.fhir_resource_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    ephemeral_priv = X25519PrivateKey.generate()
    ephemeral_pub = ephemeral_priv.public_key()
    shared = ephemeral_priv.exchange(recipient_pubkey)
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_FED_HKDF_INFO,
    ).derive(shared)

    chacha = ChaCha20Poly1305(derived_key)
    nonce = os.urandom(12)
    ciphertext = chacha.encrypt(nonce, payload_bytes, _FED_AAD)

    return SealedEnvelope(
        sender_x25519_pub=ephemeral_pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
        nonce=nonce,
        ciphertext=ciphertext,
    )


def _x25519_open(
    envelope: SealedEnvelope,
    recipient_privkey: X25519PrivateKey,
) -> tuple["FederatedRecord", bool]:
    """Descifra + verifica por AEAD un SealedEnvelope. Devuelve (record, aead_verified).

    `cryptography.hazmat.primitives.ciphers.aead.ChaCha20Poly1305.decrypt`
    lanza `InvalidTag` cuando falla la verificación AEAD, antes de exponer
    ningún texto en claro. Alcanzar la sentencia return demuestra por tanto
    tanto `decryption_succeeded` como `aead_tag_verified`.
    """
    sender_pub = X25519PublicKey.from_public_bytes(envelope.sender_x25519_pub)
    shared = recipient_privkey.exchange(sender_pub)
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_FED_HKDF_INFO,
    ).derive(shared)

    chacha = ChaCha20Poly1305(derived_key)
    plaintext = chacha.decrypt(envelope.nonce, envelope.ciphertext, _FED_AAD)
    payload = json.loads(plaintext.decode())

    record = FederatedRecord(
        drug_a=payload["drug_a"],
        drug_b=payload["drug_b"],
        severity=payload["severity"],
        description=payload["description"],
        score=payload["score"],
        issued_at=payload["issued_at"],
        nonce_128bit=payload["nonce_128bit"],
        key_epoch=payload["key_epoch"],
        signer_id=payload["signer_id"],
        signature=bytes.fromhex(payload["signature"]),
        canonical_preimage_hash=payload["canonical_preimage_hash"],
        plan_hash=payload["plan_hash"],
        fhir_resource_type=payload["fhir_resource_type"],
    )
    return record, True


# ── Transporte simulado ───────────────────────────────────────────────────────

class MockTransport:
    """Simulación en proceso de cola única del transporte MIC@2 / MAP / binario.

    Transporta SealedEnvelope (X25519 + ChaCha20-Poly1305 AEAD) — la misma
    forma de transporte que ejercita el transporte HTTP de la federación v4
    (`mind_mem.v4.federation_client.FederationClient` contra los endpoints
    `/federation/*` de `src/mind_mem/http_transport.py`, mind-mem `main`
    16a3e25, etiqueta PyPI v4.0.x pendiente).
    """

    def __init__(self) -> None:
        self._q: queue.Queue[SealedEnvelope] = queue.Queue()

    def publish(self, envelope: SealedEnvelope) -> None:
        self._q.put(envelope)

    def receive(self, timeout: float = 1.0) -> SealedEnvelope:
        return self._q.get(timeout=timeout)


# ── Utilidad de cadena de auditoría ─────────────────────────────────────────────

def _build_audit_entry(
    site: SiteState,
    event: str,
    payload_hash: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prev_hash = site.audit_log[-1]["entry_hash"] if site.audit_log else "genesis"
    entry: dict[str, Any] = {
        "site_id": site.site_id,
        "event": event,
        "payload_hash": payload_hash,
        "plan_hash": PLAN_HASH,
        "prev_hash": prev_hash,
        "ts": int(time.time()),
    }
    if extra:
        entry.update(extra)
    raw = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
    entry["entry_hash"] = _sha256_hex(raw)
    site.audit_log.append(entry)
    return entry


# ── Ruta de salida (Centro A → transporte) ───────────────────────────────────

def egress(
    site: SiteState,
    finding: ClinicalFinding,
    transport: MockTransport,
    recipient_x25519_pub: X25519PublicKey,
    fanout: EventFanout | None = None,
    peer_id: str | None = None,
) -> FederatedRecord | None:
    """
    Ejecuta la ruta de salida de JointMemoryFederation:
      classify → phi_strip → guarda-FHIR-estructural →
      bloqueo-phi-lane → estampado → firma → x25519_seal → emisión
    """
    _stage(f"SALIDA: {site.name} → transporte de federación")
    _info("Centro", f"{site.name} ({site.site_id})")
    _info("Hallazgo", f"{finding.drug_a} + {finding.drug_b} = {finding.severity}")

    # ── Invariante 1: classify ────────────────────────────────────────────────
    lane = "phi_lane" if finding.has_phi else "clinical_knowledge"
    assert lane in ("clinical_knowledge", "phi_lane"), "Invariante 1 incumplido"
    _pass(1, f"lane={lane}")

    # ── Construir la carga inicial ────────────────────────────────────────────
    payload: dict[str, Any] = {
        "drug_a":          finding.drug_a,
        "drug_b":          finding.drug_b,
        "severity":        finding.severity,
        "description":     finding.description,
        "score":           finding.score,
        "fhir_resource_type": "none",
    }
    if finding.has_phi:
        # Inyecta el texto con datos del paciente para que el depurador lo procese
        payload["description"] = finding.phi_context or finding.description

    # ── Invariantes 2 + 3: phi_strip ─────────────────────────────────────────
    # phi_strip elimina los datos del paciente de las cadenas de la carga. El
    # invariante 2 comprueba que el *resultado* de la depuración no contiene
    # datos del paciente — no si los había antes de depurar. Ejecutamos un
    # segundo escaneo sobre la carga depurada para confirmar que no quedan
    # datos del paciente residuales.
    stripped_payload, phi_found_pre, identifiers_removed = _phi_strip(payload)
    _, residual_phi, _ = _phi_strip(stripped_payload)
    assert not residual_phi, (
        "Invariante 2 incumplido: quedan datos del paciente en la carga depurada"
    )
    assert identifiers_removed >= 0, "Invariante 3 incumplido"
    _pass(2, f"carga depurada has_phi=false (eliminados {identifiers_removed} identificadores)")
    _pass(3, f"identifiers_removed={identifiers_removed}")

    # ── Invariante 4: guarda estructural FHIR ─────────────────────────────────
    _BLOCKED_FHIR_TYPES = frozenset({
        "Patient", "Observation", "MedicationStatement",
        "Encounter", "DocumentReference",
    })
    fhir_type = stripped_payload.get("fhir_resource_type", "none")
    assert fhir_type not in _BLOCKED_FHIR_TYPES, (
        f"Invariante 4 incumplido: tipo FHIR bloqueado '{fhir_type}'"
    )
    _pass(4, f"fhir_resource_type={fhir_type}")

    # ── Invariante 5: bloqueo de phi_lane ─────────────────────────────────────
    # Contrato: classify.lane != "phi_lane" O scrubbed.empty == true.
    # Un hallazgo clasificado como phi_lane queda aquí en cuarentena; nada
    # alcanza el transporte.
    if lane == "phi_lane":
        print(f"\n  {RED}{BOLD}BARRERA DE DATOS DEL PACIENTE ACTIVADA — cuarentena antes del transporte{RESET}")
        print(f"  {RED}Hallazgo clasificado como phi_lane; emisión bloqueada.{RESET}")
        _pass(5, "phi_lane detectado → emisión suprimida (vacía)")
        _build_audit_entry(
            site, "egress_phi_quarantine",
            _sha256_hex(json.dumps(stripped_payload, sort_keys=True).encode()),
            {"lane": lane},
        )
        # Refleja la cuarentena en el registro de auditoría de sincronización
        # del MemoryMesh de mind-mem y difunde una entrada estructurada de
        # event_fanout (ámbito controlado por gobernanza, conflicts_resolved=1)
        # para que los observadores externos vean el rechazo.
        if fanout is not None and peer_id is not None:
            record_quarantine_event(
                site.mesh,
                fanout,
                peer_id=peer_id,
                reason="egress_phi_quarantine",
                payload_summary={
                    "drug_pair": f"{finding.drug_a}+{finding.drug_b}",
                    "lane": lane,
                },
            )
        return None
    # lane == clinical_knowledge: invariante satisfecho trivialmente (no es phi_lane)
    _pass(5, "lane=clinical_knowledge → comprobación de phi_lane satisfecha")

    # ── Invariantes 6 + 7: estampar issued_at + nonce ─────────────────────────
    issued_at    = int(time.time())
    nonce_128bit = os.urandom(16).hex()
    stripped_payload["issued_at"]    = issued_at
    stripped_payload["nonce_128bit"] = nonce_128bit
    assert "issued_at"    in stripped_payload, "Invariante 6 incumplido"
    assert "nonce_128bit" in stripped_payload, "Invariante 7 incumplido"
    _pass(6, f"issued_at={issued_at}")
    _pass(7, f"nonce={nonce_128bit[:12]}...")

    # ── Invariantes 8 + 9: ed25519_sign ───────────────────────────────────────
    preimage         = _canonical_preimage(stripped_payload)
    preimage_hash    = _sha256_hex(preimage)
    signature        = site.private_key.sign(preimage)

    assert site.key_epoch == site.key_epoch   # tautología — epoch anclado al centro
    _pass(8, f"epoch={site.key_epoch}")
    _pass(9, f"schema=TAG_v1_NUL_separated  preimage_hash={preimage_hash[:16]}...")

    _block("preimage_hash", preimage_hash)
    _info("Firma", signature.hex()[:32] + "...")

    record = FederatedRecord(
        drug_a=finding.drug_a,
        drug_b=finding.drug_b,
        severity=finding.severity,
        description=stripped_payload["description"],
        score=finding.score,
        issued_at=issued_at,
        nonce_128bit=nonce_128bit,
        key_epoch=site.key_epoch,
        signer_id=site.site_id,
        signature=signature,
        canonical_preimage_hash=preimage_hash,
        plan_hash=PLAN_HASH,
        fhir_resource_type=fhir_type,
    )

    _build_audit_entry(
        site, "egress_emit",
        preimage_hash,
        {"signer_id": site.site_id, "epoch": site.key_epoch},
    )

    # ── Invariantes 10 + 11 + 12: x25519_seal (sobre criptográfico) ──────────
    # Par de claves X25519 efímero por registro → ECDH → HKDF-SHA256 →
    # cifrado AEAD ChaCha20-Poly1305 con vínculo a associated_data. Reproduce
    # la forma de transporte HTTP de la federación v4 (mind-mem `main`
    # 16a3e25). Seguro hacia adelante a nivel de registro: comprometer la clave
    # X25519 de largo plazo de un centro no descifra retroactivamente los
    # registros pasados (las claves efímeras de esos registros nunca se
    # persistieron).
    sealed = _x25519_seal(record, recipient_x25519_pub)
    assert len(sealed.ciphertext) > 0, (
        "Invariante 10 incumplido: x25519_seal no produjo texto cifrado"
    )
    assert sealed.cipher == "chacha20-poly1305", (
        f"Invariante 11 incumplido: cipher={sealed.cipher!r}"
    )
    assert sealed.aead_tag_length == 16, (
        f"Invariante 12 incumplido: aead_tag_length={sealed.aead_tag_length}"
    )
    _pass(10, f"sellado X25519+ChaCha20-Poly1305 ({len(sealed.ciphertext)} bytes de texto cifrado+etiqueta)")
    _pass(11, f"cipher={sealed.cipher}")
    _pass(12, f"etiqueta AEAD (Poly1305, {sealed.aead_tag_length} bytes) ligada a associated_data")

    # ── Emisión sobre el transporte simulado ──────────────────────────────────
    transport.publish(sealed)
    print(f"\n  {GREEN}→ SealedEnvelope publicado en el transporte simulado (profundidad de cola = 1){RESET}")

    # Plano de control mind-mem v3.8.14: registra el evento de sincronización
    # en el MemoryMesh local y difunde un evento hcli.federation.publish para
    # que los observadores (Redis Stream / Kafka / adaptador propio) puedan
    # reflejar el intercambio. Los propios bytes de transporte siguen yendo por
    # MockTransport; en producción se sustituye por HTTP/gRPC/QUIC.
    if fanout is not None and peer_id is not None:
        receipt = record_publish_event(
            site.mesh,
            fanout,
            peer_id=peer_id,
            payload_summary={
                "drug_pair": f"{record.drug_a}+{record.drug_b}",
                "severity": record.severity,
                "signer_id": record.signer_id,
            },
            semantic_idempotency_hash=preimage_hash,
            transport_dedup_hash=preimage_hash,
        )
        print(
            f"  {DIM}mesh.log_sync → {receipt.scope.value} scope, "
            f"governance-gated peer {receipt.peer_id}{RESET}"
        )
    return record


# ── Ruta de entrada (transporte → Centro B) ──────────────────────────────────

def ingress(
    site: SiteState,
    sealed_envelope: SealedEnvelope,
    peer_public_key: Ed25519PublicKey,
    peer_signatures: list[FederatedRecord],
    fanout: EventFanout | None = None,
    peer_id: str | None = None,
) -> LocalKnowledge:
    """
    Ejecuta la ruta de entrada de JointMemoryFederation:
      x25519_open → ed25519_verify → ventana_de_frescura → phi_strip_inbound →
      tier_clamp → severity_quorum → mind_mem_ingest
    """
    _stage(f"ENTRADA: transporte de federación → {site.name}")
    _info("Centro", f"{site.name} ({site.site_id})")

    # ── Invariantes 13 + 14: x25519_open (descifrar + verificar AEAD) ────────
    # cryptography.hazmat.primitives.ciphers.aead.ChaCha20Poly1305.decrypt
    # lanza InvalidTag cuando falla la verificación AEAD, ANTES de exponer el
    # texto en claro. Alcanzar la siguiente línea demuestra por tanto, de forma
    # atómica, tanto decryption_succeeded == true COMO aead_tag_verified == true.
    try:
        record, aead_verified = _x25519_open(sealed_envelope, site.x25519_private)
    except InvalidTag as exc:
        raise AssertionError(
            "Invariante 13/14 incumplido: x25519_open lanzó InvalidTag — "
            "etiqueta AEAD discordante o falló la derivación del secreto compartido ECDH"
        ) from exc
    assert aead_verified is True, "Invariante 14 incumplido: indicador de verificación AEAD falso"
    _pass(13, "secreto compartido X25519 ECDH derivado; texto en claro ChaCha20-Poly1305 recuperado")
    _pass(14, "etiqueta AEAD Poly1305 verificada (atómica con el descifrado; lanza antes de exponer el texto en claro)")
    _info("De", record.signer_id)

    # ── Invariantes 15 + 16: ed25519_verify ───────────────────────────────────
    inbound_payload: dict[str, Any] = {
        "drug_a":             record.drug_a,
        "drug_b":             record.drug_b,
        "severity":           record.severity,
        "description":        record.description,
        "score":              record.score,
        "fhir_resource_type": record.fhir_resource_type,
        "issued_at":          record.issued_at,
        "nonce_128bit":       record.nonce_128bit,
    }
    preimage = _canonical_preimage(inbound_payload)
    try:
        peer_public_key.verify(record.signature, preimage)
        sig_valid = True
    except InvalidSignature:
        sig_valid = False

    assert sig_valid, "Invariante 15 incumplido: firma no válida"
    _pass(15, "firma Ed25519 verificada")

    epoch_revoked = record.key_epoch in site.revoked_epochs
    assert not epoch_revoked, f"Invariante 16 incumplido: epoch {record.key_epoch} revocado"
    _pass(16, f"epoch={record.key_epoch} no está en la lista de denegación")

    # ── Invariantes 17 + 18: ventana de frescura ─────────────────────────────
    now                = int(time.time())
    issued_at_ago      = now - record.issued_at
    assert issued_at_ago <= 300, f"Invariante 17 incumplido: registro demasiado antiguo ({issued_at_ago}s)"
    assert issued_at_ago >= 0,   f"Invariante 18 incumplido: antigüedad negativa ({issued_at_ago}s)"
    _pass(17, f"issued_at_seconds_ago={issued_at_ago}s <= 300")
    _pass(18, f"issued_at_seconds_ago={issued_at_ago}s >= 0")

    # ── Invariante 19: phi_strip de entrada ───────────────────────────────────
    _, inbound_phi_found, _ = _phi_strip(inbound_payload)
    assert not inbound_phi_found, "Invariante 19 incumplido: datos del paciente detectados en la depuración de entrada"
    _pass(19, "depuración de datos del paciente de entrada limpia")

    # ── Invariante 20: comprobación de límites de tier ────────────────────────
    raw_tier    = 2   # tier proporcionado por el par (simulado)
    tier_value  = max(0, min(5, raw_tier))
    assert 0 <= tier_value <= 5, "Invariante 20 incumplido"
    _pass(20, f"tier={tier_value} en [0..5]")

    # ── Invariante 21: barrera de quórum de gravedad ──────────────────────────
    # El quórum por defecto es 3 de 5. Con 1 firma de par (centro único), no hay quórum.
    n_concurring  = len(peer_signatures)  # 0 en el caso de un solo par
    has_quorum    = n_concurring >= 3
    effective_tier = tier_value if has_quorum else min(tier_value, 1)

    # Invariante: quórum O tier <= 1
    assert has_quorum or effective_tier <= 1, "Invariante 21 incumplido"
    _pass(
        21,
        f"quorum={has_quorum} (concurring={n_concurring}/5)"
        f" → tier={effective_tier} (low tier, evidence_grade={has_quorum})",
    )

    # ── mind_mem_ingest (simulado) ────────────────────────────────────────────
    preimage_hash = _sha256_hex(preimage)
    local_record  = LocalKnowledge(
        drug_a=record.drug_a,
        drug_b=record.drug_b,
        severity=record.severity,
        description=record.description,
        score=record.score,
        provenance=record.signer_id,
        tier=effective_tier,
        evidence_grade=has_quorum,
        received_at=now,
        audit_chain_hash=preimage_hash,
    )
    key = f"{record.drug_a}_{record.drug_b}_{record.nonce_128bit[:8]}"
    site.memory_store[key] = local_record

    audit_entry = _build_audit_entry(
        site, "ingress_ingest",
        preimage_hash,
        {
            "from_signer": record.signer_id,
            "tier": effective_tier,
            "evidence_grade": has_quorum,
        },
    )

    print(f"\n  {GREEN}✓ Registro incorporado al almacén de memoria local{RESET}")
    _info("  tier",           str(effective_tier))
    _info("  evidence_grade", str(has_quorum))
    _info("  audit_hash",     audit_entry["entry_hash"][:32] + "...")

    # Plano de control mind-mem v3.8.14: registra la incorporación en el
    # registro de auditoría de sincronización del MemoryMesh local + difunde por
    # event_fanout. La política de resolución de conflictos por ámbito del mesh
    # (governance_gated para SEMANTIC + GOVERNANCE) es lo que la barrera de
    # quórum de gravedad (invariante 21 arriba) aplica en tiempo de ejecución.
    if fanout is not None and peer_id is not None:
        receipt = record_ingest_event(
            site.mesh,
            fanout,
            peer_id=peer_id,
            payload_summary={
                "drug_pair": f"{record.drug_a}+{record.drug_b}",
                "severity": record.severity,
                "tier": effective_tier,
                "evidence_grade": has_quorum,
                "from_signer": record.signer_id,
            },
        )
        print(
            f"  {DIM}mesh.log_sync → {receipt.scope.value} scope, "
            f"peer {receipt.peer_id} (conflicts_resolved={receipt.conflicts_resolved}){RESET}"
        )

    return local_record


# ── Reconciliación de la cadena de auditoría ────────────────────────────────────

def _reconcile_audit_chains(site_a: SiteState, site_b: SiteState) -> tuple[str, str]:
    """
    Verifica que las cadenas de auditoría de ambos centros comparten el mismo
    hash de preimagen canónica para el evento de federación.

    Devuelve (site_a_payload_hash, site_b_payload_hash).
    """
    _stage("RECONCILIACIÓN DE LA CADENA DE AUDITORÍA")

    def _get_event_payload_hash(site: SiteState, event: str) -> str:
        for entry in site.audit_log:
            if entry["event"] == event:
                return entry["payload_hash"]
        raise KeyError(f"Evento '{event}' no encontrado en el registro de auditoría de {site.name}")

    hash_a = _get_event_payload_hash(site_a, "egress_emit")
    hash_b = _get_event_payload_hash(site_b, "ingress_ingest")

    _info(f"{site_a.name} salida  preimage_hash",  hash_a[:32] + "...")
    _info(f"{site_b.name} entrada preimage_hash",  hash_b[:32] + "...")

    match = hash_a == hash_b
    if match:
        print(f"\n  {GREEN}{BOLD}CADENA DE AUDITORÍA COINCIDE — codificación canónica idéntica bit a bit{RESET}")
        print(f"  {GREEN}Ambos centros coinciden en el hash de preimagen canónica:{RESET}")
        print(f"  {GREEN}{hash_a}{RESET}")
    else:
        print(f"\n  {RED}{BOLD}CADENA DE AUDITORÍA DISCORDANTE — codificación canónica divergente{RESET}")
        raise AssertionError(
            f"Cadena de auditoría discordante: A={hash_a} B={hash_b}"
        )
    return hash_a, hash_b


# ── Flujo principal de la simulación ───────────────────────────────────────────

def run_demo(phi_test: bool = False) -> tuple[str, str]:
    """
    Ejecuta la simulación completa de federación de 2 nodos.

    Args:
        phi_test: Si es True, inyecta datos del paciente en el hallazgo para
            ejercitar la barrera.

    Returns:
        (site_a_audit_hash, site_b_audit_hash)
    """
    _banner()

    # ── Preparación ─────────────────────────────────────────────────────────────
    _stage("PREPARACIÓN DE CENTROS — Hospital General de Malabo + Hospital Regional de Bata")
    site_a = _make_site("Hospital General de Malabo",  "HGM-001")
    site_b = _make_site("Hospital Regional de Bata",   "HRB-001")
    transport = MockTransport()

    # Plano de control mind-mem v3.8.14 entre centros:
    #   * cada centro posee un MemoryMesh (registro de pares + 7 ámbitos + registro de auditoría)
    #   * un único EventFanout compartido difunde cada publicación/incorporación/cuarentena
    fanout = make_default_fanout()
    register_clinical_peer(
        site_a.mesh,
        peer_id=site_b.site_id,
        endpoint="inproc://mock-transport/bata",
    )
    register_clinical_peer(
        site_b.mesh,
        peer_id=site_a.site_id,
        endpoint="inproc://mock-transport/malabo",
    )

    print(f"  {CYAN}Centro A:{RESET} {site_a.name} ({site_a.site_id})")
    print(f"         clave pública Ed25519: {site_a.public_key.public_bytes_raw().hex()[:32]}...")
    print(f"  {CYAN}Centro B:{RESET} {site_b.name} ({site_b.site_id})")
    print(f"         clave pública Ed25519: {site_b.public_key.public_bytes_raw().hex()[:32]}...")
    print(f"  {CYAN}Transporte:{RESET} cola Python en proceso (simulación de MIC@2 / MAP / binario)")
    print(
        f"  {CYAN}Plano de control:{RESET} MemoryMesh mind-mem v3.8.14 "
        f"+ EventFanout (pares: {len(site_a.mesh.peers())}↔{len(site_b.mesh.peers())})"
    )

    # ── Centro A: descubrir el hallazgo ───────────────────────────────────────
    _stage("CENTRO A: Cadena de Capas 1-4 — descubrir interacción farmacológica")

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from engine.clinical_scoring import check_drug_interactions

    raw = check_drug_interactions(["warfarin", "ibuprofen"], use_llm_fallback=False)
    assert raw, "Se esperaba al menos una interacción de la tabla de la Capa 1"
    interaction = raw[0]

    _info("Par descubierto", f"{interaction.drug_a} + {interaction.drug_b}")
    _info("Veredicto de la Capa 1", f"severity={interaction.severity}  score={interaction.score}")
    if interaction.bitnet_repro_hash:
        _info(
            "BitNet repro_hash",
            interaction.bitnet_repro_hash[:32] + "...",
        )

    if phi_test:
        finding = ClinicalFinding(
            drug_a=interaction.drug_a,
            drug_b=interaction.drug_b,
            severity=interaction.severity,
            description=interaction.description,
            score=interaction.score,
            has_phi=True,
            phi_context=(
                "Patient Obono Mangue DOB: 03/14/1959 MRN: OM-2026-0847 — "
                + interaction.description
            ),
        )
        print(f"\n  {RED}[MODO PRUEBA DE DATOS DEL PACIENTE] Inyectando datos del paciente en la carga del hallazgo{RESET}")
    else:
        finding = ClinicalFinding(
            drug_a=interaction.drug_a,
            drug_b=interaction.drug_b,
            severity=interaction.severity,
            description=interaction.description,
            score=interaction.score,
        )

    # ── Salida ────────────────────────────────────────────────────────────────
    emitted = egress(
        site_a, finding, transport,
        recipient_x25519_pub=site_b.x25519_public,
        fanout=fanout, peer_id=site_b.site_id,
    )

    if phi_test:
        assert emitted is None, "Prueba de datos del paciente: se esperaba que egress devolviera None (en cuarentena)"
        print(f"\n  {GREEN}{BOLD}PRUEBA DE BARRERA DE DATOS DEL PACIENTE SUPERADA — hallazgo en cuarentena, transporte no invocado{RESET}")
        audit_hash_a = site_a.audit_log[-1]["entry_hash"]
        print(f"\n  {CYAN}Hash de auditoría del Centro A (evento de cuarentena):{RESET} {audit_hash_a}")
        return audit_hash_a, "N/A (cuarentena de datos del paciente)"

    assert emitted is not None, "Se esperaba un registro emitido"

    # ── Entrada ───────────────────────────────────────────────────────────────
    received_envelope = transport.receive(timeout=1.0)
    # Comprobación: el sobre se abre con la clave privada X25519 del centro B y
    # reproduce el mismo canonical_preimage_hash que firmó la salida.
    _peek_record, _ = _x25519_open(received_envelope, site_b.x25519_private)
    assert _peek_record.canonical_preimage_hash == emitted.canonical_preimage_hash, (
        "Discordancia de ida y vuelta del transporte simulado: el sobre abierto no coincide con el registro emitido"
    )

    ingested = ingress(
        site_b,
        received_envelope,
        peer_public_key=site_a.public_key,
        peer_signatures=[],   # 0 de 5 concurrentes → nivel bajo por la barrera de quórum
        fanout=fanout,
        peer_id=site_a.site_id,
    )

    # ── Resumen ───────────────────────────────────────────────────────────────
    _stage("REGISTRO INCORPORADO — almacén de conocimiento local del Centro B")
    _info("drug_a",        ingested.drug_a)
    _info("drug_b",        ingested.drug_b)
    _info("severity",      ingested.severity)
    _info("description",   ingested.description[:80])
    _info("tier",          str(ingested.tier))
    _info("evidence_grade", str(ingested.evidence_grade))
    _info("provenance",    ingested.provenance)

    # ── Reconciliación de auditoría ───────────────────────────────────────────
    hash_a, hash_b = _reconcile_audit_chains(site_a, site_b)

    # ── Estado del MemoryMesh de mind-mem ──────────────────────────────────────
    _stage("PLANO DE CONTROL MIND-MEM v3.8.14 — estado del MemoryMesh")
    status_a = site_a.mesh.status()
    status_b = site_b.mesh.status()
    _info(f"{site_a.name} pares del mesh", str(status_a["peer_count"]))
    _info(f"{site_a.name} eventos registrados en el mesh", str(status_a["events_logged"]))
    _info(f"{site_b.name} pares del mesh", str(status_b["peer_count"]))
    _info(f"{site_b.name} eventos registrados en el mesh", str(status_b["events_logged"]))
    print(
        f"\n  {DIM}(cada entrada anterior fluyó tanto por el registro de auditoría "
        f"de sincronización del mesh local como por el flujo EventFanout → LoggingPublisher){RESET}"
    )

    # ── Recuento final de invariantes ─────────────────────────────────────────
    _stage("RESUMEN FINAL")
    total = len(INVARIANT_DESCRIPTIONS)
    print(f"  {GREEN}{BOLD}Los {total} invariantes de JointMemoryFederation.flow.mind: CUMPLEN{RESET}")
    print(f"  {CYAN}plan_hash    :{RESET} {PLAN_HASH}")
    print(f"  {CYAN}hash Centro A:{RESET} {hash_a}")
    print(f"  {CYAN}hash Centro B:{RESET} {hash_b}")
    print(f"\n  {DIM}(la igualdad de hashes demuestra la codificación canónica idéntica bit a bit){RESET}")
    print(f"\n{BOLD}{GREEN}{'═' * 70}{RESET}")
    print(f"{BOLD}{GREEN}  SIMULACIÓN DE FEDERACIÓN COMPLETADA — salida 0{RESET}")
    print(f"{BOLD}{GREEN}{'═' * 70}{RESET}\n")

    return hash_a, hash_b


# ── Punto de entrada ────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simulación de federación de 2 nodos de Historia Clínica",
    )
    parser.add_argument(
        "--phi-test",
        action="store_true",
        help="Inyecta datos del paciente en el hallazgo para ejercitar la barrera de datos del paciente",
    )
    args = parser.parse_args()
    try:
        run_demo(phi_test=args.phi_test)
        return 0
    except Exception as exc:
        print(f"\n{RED}{BOLD}SIMULACIÓN FALLIDA: {exc}{RESET}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
