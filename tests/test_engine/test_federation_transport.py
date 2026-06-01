"""Pruebas unitarias de engine/federation_transport.py.

Verifica el puente hacia MemoryMesh + EventFanout de mind-mem v3.8.14:

* :func:`make_site_mesh` devuelve un MemoryMesh con la política
  documentada de resolución de conflictos por defecto y por ámbito.
* :func:`register_clinical_peer` registra con los ámbitos semantic +
  governance — los dos ámbitos que necesita la compuerta de cuórum por
  severidad (invariante 16 en flows/JointMemoryFederation.flow.mind).
* :func:`record_publish_event` escribe una entrada de sincronización y
  emite un evento.
* :func:`record_ingest_event` reproduce la misma forma.
* :func:`record_quarantine_event` escribe ``conflicts_resolved=1`` para
  que el rechazo sea visible en el registro de auditoría de
  sincronización.
"""
from __future__ import annotations

import pytest

from mind_mem.event_fanout import Event, Publisher
from mind_mem.memory_mesh import ConflictResolution, SyncScope

from engine.federation_transport import (
    CLINICAL_PEER_SCOPES,
    EVENT_FEDERATION_INGEST,
    EVENT_FEDERATION_PUBLISH,
    EVENT_FEDERATION_QUARANTINE,
    make_default_fanout,
    make_site_mesh,
    record_ingest_event,
    record_publish_event,
    record_quarantine_event,
    register_clinical_peer,
)


class _CapturingPublisher:
    """Publicador de prueba que registra cada evento que recibe."""

    name = "capturing"

    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)

    def close(self) -> None:
        return None


@pytest.fixture()
def capture() -> _CapturingPublisher:
    return _CapturingPublisher()


@pytest.fixture()
def fanout(capture: _CapturingPublisher):
    # Añade el publicador de captura además del LoggingPublisher para
    # poder verificar las cargas de los eventos sin analizar líneas de
    # registro.
    return make_default_fanout(extra_publishers=(capture,))


# ── make_site_mesh ────────────────────────────────────────────────────────────


def test_make_site_mesh_starts_empty() -> None:
    mesh = make_site_mesh()
    status = mesh.status()
    assert status["peer_count"] == 0
    assert status["events_logged"] == 0


def test_make_site_mesh_has_governance_gated_semantic_policy() -> None:
    """Los ámbitos semantic + governance deben ser governance_gated por defecto."""
    mesh = make_site_mesh()
    status = mesh.status()
    assert status["policy"]["semantic"] == "governance_gated"
    assert status["policy"]["governance"] == "governance_gated"
    # Los ámbitos del nivel caliente se quedan en last_write_wins
    assert status["policy"]["memories"] == "last_write_wins"
    assert status["policy"]["actions"] == "last_write_wins"


# ── register_clinical_peer ────────────────────────────────────────────────────


def test_register_clinical_peer_uses_clinical_scopes() -> None:
    mesh = make_site_mesh()
    peer = register_clinical_peer(
        mesh,
        peer_id="MGH-001",
        endpoint="https://mgh.example/federation",
    )
    assert peer.peer_id == "MGH-001"
    assert tuple(peer.scopes) == CLINICAL_PEER_SCOPES
    assert SyncScope.SEMANTIC in peer.scopes
    assert SyncScope.GOVERNANCE in peer.scopes


def test_register_clinical_peer_appears_in_status() -> None:
    mesh = make_site_mesh()
    register_clinical_peer(
        mesh,
        peer_id="MAYO-001",
        endpoint="https://mayo.example/federation",
    )
    status = mesh.status()
    assert status["peer_count"] == 1
    assert status["peers"][0]["peer_id"] == "MAYO-001"
    assert "semantic" in status["peers"][0]["scopes"]
    assert "governance" in status["peers"][0]["scopes"]


# ── record_publish_event ──────────────────────────────────────────────────────


def test_record_publish_event_logs_sync_and_emits_event(
    fanout, capture: _CapturingPublisher
) -> None:
    mesh = make_site_mesh()
    register_clinical_peer(
        mesh, peer_id="MAYO-001", endpoint="inproc://mock/mayo"
    )

    receipt = record_publish_event(
        mesh,
        fanout,
        peer_id="MAYO-001",
        payload_summary={"drug_pair": "warfarin+ibuprofen", "severity": "major"},
        semantic_idempotency_hash="deadbeef",
        transport_dedup_hash="cafef00d",
    )

    # El recibo refleja la entrada
    assert receipt.peer_id == "MAYO-001"
    assert receipt.scope == SyncScope.SEMANTIC
    assert receipt.semantic_idempotency_hash == "deadbeef"
    assert receipt.transport_dedup_hash == "cafef00d"

    # El mesh registró la sincronización
    status = mesh.status()
    assert status["events_logged"] == 1
    audit = mesh.audit_log()
    assert len(audit) == 1
    assert audit[0]["peer_id"] == "MAYO-001"
    assert audit[0]["scope"] == "semantic"
    assert audit[0]["resolution"] == "governance_gated"
    assert audit[0]["blocks_transferred"] == 1
    assert audit[0]["conflicts_resolved"] == 0

    # El fanout emitió exactamente un evento con el tipo y la carga esperados
    assert len(capture.events) == 1
    event = capture.events[0]
    assert event.kind == EVENT_FEDERATION_PUBLISH
    assert event.payload["peer_id"] == "MAYO-001"
    assert event.payload["semantic_idempotency_hash"] == "deadbeef"
    assert event.payload["transport_dedup_hash"] == "cafef00d"
    assert event.payload["drug_pair"] == "warfarin+ibuprofen"
    assert event.payload["severity"] == "major"


# ── record_ingest_event ───────────────────────────────────────────────────────


def test_record_ingest_event_logs_sync_and_emits_event(
    fanout, capture: _CapturingPublisher
) -> None:
    mesh = make_site_mesh()
    register_clinical_peer(
        mesh, peer_id="MGH-001", endpoint="inproc://mock/mgh"
    )

    receipt = record_ingest_event(
        mesh,
        fanout,
        peer_id="MGH-001",
        payload_summary={
            "drug_pair": "warfarin+ibuprofen",
            "tier": 1,
            "evidence_grade": False,
        },
    )

    assert receipt.peer_id == "MGH-001"
    assert receipt.scope == SyncScope.SEMANTIC
    assert receipt.conflicts_resolved == 0

    audit = mesh.audit_log()
    assert len(audit) == 1
    assert audit[0]["resolution"] == "governance_gated"

    assert len(capture.events) == 1
    event = capture.events[0]
    assert event.kind == EVENT_FEDERATION_INGEST
    assert event.payload["peer_id"] == "MGH-001"
    assert event.payload["tier"] == 1
    assert event.payload["evidence_grade"] is False


# ── record_quarantine_event ───────────────────────────────────────────────────


def test_record_quarantine_event_marks_conflict_resolved(
    fanout, capture: _CapturingPublisher
) -> None:
    """Los eventos de cuarentena marcan conflicts_resolved=1 para que el personal detecte los rechazos."""
    mesh = make_site_mesh()
    register_clinical_peer(
        mesh, peer_id="MAYO-001", endpoint="inproc://mock/mayo"
    )

    record_quarantine_event(
        mesh,
        fanout,
        peer_id="MAYO-001",
        reason="egress_phi_quarantine",
        payload_summary={"drug_pair": "warfarin+ibuprofen", "lane": "phi_lane"},
    )

    audit = mesh.audit_log()
    assert len(audit) == 1
    assert audit[0]["scope"] == "governance"
    assert audit[0]["conflicts_resolved"] == 1
    assert audit[0]["blocks_transferred"] == 0

    assert len(capture.events) == 1
    event = capture.events[0]
    assert event.kind == EVENT_FEDERATION_QUARANTINE
    assert event.payload["peer_id"] == "MAYO-001"
    assert event.payload["reason"] == "egress_phi_quarantine"
    assert event.payload["lane"] == "phi_lane"


# ── End-to-end integration ────────────────────────────────────────────────────


def test_publish_then_ingest_round_trip(
    fanout, capture: _CapturingPublisher
) -> None:
    """Dos centros hacen un ciclo completo de un hallazgo por el puente: publicar e ingerir emiten un evento cada uno."""
    mesh_a = make_site_mesh()
    mesh_b = make_site_mesh()
    register_clinical_peer(mesh_a, peer_id="MAYO-001", endpoint="inproc://mayo")
    register_clinical_peer(mesh_b, peer_id="MGH-001", endpoint="inproc://mgh")

    record_publish_event(
        mesh_a,
        fanout,
        peer_id="MAYO-001",
        payload_summary={"drug_pair": "warfarin+ibuprofen"},
        semantic_idempotency_hash="abc",
        transport_dedup_hash="def",
    )
    record_ingest_event(
        mesh_b,
        fanout,
        peer_id="MGH-001",
        payload_summary={"drug_pair": "warfarin+ibuprofen"},
    )

    # Cada lado registró una sincronización; el fanout vio dos eventos.
    assert mesh_a.status()["events_logged"] == 1
    assert mesh_b.status()["events_logged"] == 1
    assert len(capture.events) == 2
    assert capture.events[0].kind == EVENT_FEDERATION_PUBLISH
    assert capture.events[1].kind == EVENT_FEDERATION_INGEST


def test_capturing_publisher_satisfies_publisher_protocol(
    capture: _CapturingPublisher,
) -> None:
    """El fixture de prueba debe satisfacer el protocolo Publisher de mind-mem."""
    assert isinstance(capture, Publisher)
