"""Agent tool implementations for the Historia Clínica A2A agent."""
from a2a_agent.tools.memory_tools import (
    recall_clinical_context,
    store_clinical_note,
)
from a2a_agent.tools.safety_tools import (
    medication_safety_review,
    detect_record_contradictions,
)
from a2a_agent.tools.fhir_tools import (
    get_patient_demographics,
    get_active_medications,
    get_active_conditions,
    get_recent_observations,
)

__all__ = [
    "recall_clinical_context",
    "store_clinical_note",
    "medication_safety_review",
    "detect_record_contradictions",
    "get_patient_demographics",
    "get_active_medications",
    "get_active_conditions",
    "get_recent_observations",
]
