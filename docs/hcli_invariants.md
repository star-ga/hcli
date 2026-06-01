# Invariantes Clínicos — Historia Clínica EG

> Especificación de los invariantes de seguridad que el sistema Historia Clínica
> hace cumplir de forma determinista en cada decisión clínica. Estos invariantes
> son verificables mecánicamente y quedan anclados en la cadena de auditoría
> (`repro_hash` SHA-256) de cada clasificación.
>
> Aplicados en tiempo de ejecución y en `tests/`. La compuerta arch-mind L1 los
> verificará mecánicamente cuando la versión comercial v0.2 incluya el perfil
> `clinical_invariants`.

---

## 1. Determinismo bit-idéntico

**Invariante.** Para una misma entrada, la clasificación produce un `repro_hash`
SHA-256 **idéntico byte a byte** en cualquier arquitectura (ARM, x86_64, CUDA,
NPU, navegador). El paso hacia delante usa aritmética de coma fija Q16.16 sobre
pesos ternarios {-1, 0, +1} — sin coma flotante, sin deriva entre plataformas.

**Verificación.** `tests/test_engine/test_path_a_v8_q16_determinism_pin.py` —
la misma par de fármacos reclasificada en 2026 y en 2046, en cualquier
dispositivo, debe coincidir con el `repro_hash` original.

---

## 2. Recall 100 % en la clase contraindicado

**Invariante.** El clasificador detecta el **100 %** de las interacciones
contraindicadas de la cohorte (44/44), con **0 falsos positivos** en esa clase
crítica de seguridad. Ninguna interacción contraindicada puede pasar sin alerta.

**Verificación.** `tests/test_engine/test_path_a_v8_live_recall_pin.py` y
`tests/test_engine/test_bitnet_alone_major_recall_pin.py`.

---

## 3. Integridad del paquete (9 invariantes bloqueados)

**Invariante.** Cada paquete de pesos (`bitnet_weights.json`) lleva un
`bundle_id` = SHA-256 de su carga de pesos en forma canónica. El `weights_id`
compuesto `"{a_id}+{b_id}"` aparece en cada resultado, de modo que toda
clasificación es trazable hasta los pesos exactos que la produjeron.

**Verificación.** `tests/test_engine/test_bitnet_bundle_integrity_pin.py`,
`tests/test_engine/test_bitnet_param_count_pin.py`.

---

## 4. Compuerta PHI (los datos del paciente no salen de la clínica)

**Invariante.** En la frontera de federación
(`JointMemoryFederation::classify`), los datos del paciente (PHI) se separan
deterministamente del conocimiento clínico. Ningún identificador de paciente ni
dato sensible puede salir del nodo de clínica sin pasar la compuerta PHI; cada
egreso queda registrado y firmado.

**Las 5 garantías que este anclaje hace cumplir:**
1. Toda escritura local va firmada y al registro append-only.
2. Ningún payload con PHI cruza la frontera de federación sin desidentificación.
3. El orden de los eventos es canónico → hash estable, reproducible.
4. Cada conflicto se detecta, se registra y se resuelve con un evento firmado.
5. Toda anulación de una alerta exige un motivo registrado.

**Verificación.** `tests/test_engine/test_federation_transport.py`,
`tests/test_scripts/test_federation_invariant_count_pin.py`.

---

## 5. Cadena de auditoría inviolable

**Invariante.** Cada acción clínica responde, de forma firmada y reproducible:
quién, dónde, en qué dispositivo, para qué paciente, en qué momento, con qué
entrada, con qué salida, con qué versión de la base de datos de medicamentos,
con qué versión del modelo, si el PHI tenía permiso de salida, y si el médico
anuló una alerta y por qué. Cada bloque encadena el hash del anterior
(`block.prev = sha256(prev)`), por lo que toda manipulación es detectable.

**Verificación.** `tests/test_scripts/test_reproducibility_manifest.py` — el
manifiesto de reproducibilidad (`docs/reproducibility_manifest.json`) es una
instantánea direccionada por contenido que un auditor del Ministerio de Sanidad
y Bienestar Social puede verificar con un solo comando.

---

## 6. Cobertura de explicación de contraindicaciones

**Invariante.** Cada entrada de la caché de contraindicados dispara ≥ 1 regla
determinista; **cero respaldo de brecha documentada**. Toda alerta lleva una
explicación trazable a su regla y a su evidencia.

**Verificación.** `tests/test_engine/test_contra_explanation_coverage_pin.py`,
`tests/test_engine/test_pharmacology_flags_coverage_pin.py`.

---

## 7. Cohorte de control negativo

**Invariante.** El sistema no genera alertas espurias sobre pares de fármacos
sin interacción conocida (control negativo), preservando la confianza del
clínico y evitando la fatiga de alertas.

**Verificación.** `tests/test_engine/test_negative_control_cohort_integrity_pin.py`,
`scripts/run_negative_control_eval.py`.

---

## Marco de defensa en profundidad

El clasificador BitNet es **una capa** del canal de 6 capas; los invariantes
anteriores se hacen cumplir a lo largo de todo el canal:

| Capa | Función |
|---|---|
| 1 | Tabla determinista de contraindicaciones |
| 2 | Búsqueda en la base de datos de medicamentos |
| 3 | Comprobaciones específicas del paciente |
| 4 | Motor de contraindicaciones e interacciones |
| 4.5 | Verificación / reproducibilidad BitNet (esta primitiva) |
| 5 | Generador de explicaciones |
| 6 | Compuerta de abstención / escalado |

Las capas 1–4 siguen siendo de peso para fármacos nuevos y deriva de cohorte;
el conjunto BitNet es la primitiva de re-ejecución bit-idéntica, nunca el único
clasificador. El médico es siempre el decisor final.

---

*Historia Clínica — STARGA, Inc. · Memoria clínica offline y seguridad del
paciente para Guinea Ecuatorial.*
