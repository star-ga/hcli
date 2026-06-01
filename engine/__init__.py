"""Motor de Historia Clínica — memoria clínica persistente.

Módulos:
- clinical_memory: canalización principal FHIR-a-memoria con búsqueda híbrida
- clinical_scoring: puntuación condicionada por confianza (abstención, importancia, adversarial)
- fhir_client: cliente REST FHIR R4 con protección frente a SSRF
- llm_synthesizer: generación de narrativa clínica fundamentada en evidencia
- rxnorm_client: normalización de medicamentos RxNorm y búsqueda de interacciones (API de los NIH)
- snomed_client: terminología clínica codificada SNOMED CT
- umls_mapper: mapeo de conceptos entre vocabularios mediante UMLS
"""
