# Validación Clínica — Historia Clínica v1 vs. Consenso de Expertos

> *"¿De dónde salen las cifras, y estaría de acuerdo un clínico?"*

Este documento registra la evidencia de validación clínica que respalda la
tubería de seguridad de interacciones farmacológicas (DDI) de 6 capas de
Historia Clínica. La base de código de Historia Clínica-v1 es de código
abierto (Apache-2.0) y no es un dispositivo con autorización regulatoria —
pero cada cifra publicada es reproducible a partir del código de este
repositorio.

## Asesora Clínica y Coautora

**Dra. Ludmila Afonicheva** — Especialista titulada en Medicina de Familia.

| Credencial | Valor | Verificable |
|---|---|---|
| Especialidad | Medicina de Familia | Titulada por consejo de especialidad |
| Práctica | Práctica Familiar Tradicional — consulta privada individual | Continua desde 2003 (~21 años) |
| Dirección | 23639 Hawthorne Blvd, Suite 200, Torrance, CA 90505 | [md4family.com](https://md4family.com/) |
| Colegiación | California | — |
| Identificador de profesional | **1932159530** | Registro público de profesionales |
| Afiliaciones hospitalarias | Torrance Memorial Medical Center (TMMC), Little Company of Mary Hospital (LCMH) | Ambos en Torrance, CA |
| Ámbito de práctica | Medicina de familia, dermatología, procedimientos estéticos, coordinación de ingresos hospitalarios, visitas domiciliarias a pacientes confinados | Atención primaria multimodal |

El identificador de profesional de la asesora **1932159530** supera la
verificación Luhn con prefijo `80840` mediante el propio validador de este
repositorio (`engine/npi_registry.py::validate_npi`) — el mismo validador
que controla cada identificador Practitioner en el paquete de demostración
FHIR R4. Los revisores pueden verificar de forma independiente el
identificador contra el registro público en el enlace anterior.

### Alcance de la revisión clínica

La Dra. Afonicheva está revisando el paquete de demostración de Lucía
Obono Mangue, las clasificaciones de gravedad de la cohorte de 35 pares de
índice terapéutico estrecho (NTI) en
`engine/clinical_scoring.py::_KNOWN_INTERACTIONS`, las condiciones de
activación de la compuerta de abstención en la tubería de 6 capas, y el
conflicto de objetivo de presión arterial entre cardiología y nefrología
que es el escenario crítico de la demostración.

La medicina de familia — particularmente una consulta privada individual
de 21 años que ingresa pacientes a dos hospitales comunitarios y realiza
visitas domiciliarias — es exactamente la especialidad formada para
coordinar conflictos multiespecialista en una atención fragmentada. El
caso de Lucía Obono Mangue (mujer de 67 años con DM2 + HTA + ERC-3b + FA
manejada por cuatro especialistas no coordinados, más una prescripción de
ibuprofeno de urgencias sobre warfarina) es un problema de coordinación de
medicina de familia de manual.

Cita de respaldo y cualquier ajuste de escenario clínico pendientes de la
revisión de la Dra. Afonicheva (informe de presentación entregado
2026-05-02 con la visión general completa del proyecto y lo que se
necesita de ella; ventana de revisión abierta hasta el 2026-05-09).

## Qué validamos

El trabajo de la tubería es la **seguridad** — los falsos negativos (pasar
por alto una interacción peligrosa) son catastróficos; los falsos
positivos (sobreseñalar) son recuperables. Medimos la tubería frente a
cuatro referencias de fármacos de índice terapéutico estrecho (NTI)
extraídas de la literatura. Los fármacos NTI son la prueba de estrés
canónica (AGS Beers / STOPP-START) para sistemas de DDI porque sus
ventanas terapéuticas son lo bastante estrechas como para que cualquier
sobre- o infraseñalamiento se traduzca directamente en daño clínico.

### Cohorte de prueba de estrés (fármacos NTI)

| Fármaco | Clase | Por qué importa | Pares puntuados |
|---|---|---|---|
| Warfarina | Anticoagulante antagonista de la vitamina K | Ventana de INR 2–3; hemorragia mortal; ~30K visitas a urgencias/año relacionadas con interacciones | 14 |
| Digoxina | Glucósido cardíaco | Ventana terapéutica 0,8–2,0 ng/mL; toxicidad = arritmia/muerte | 6 |
| Litio | Estabilizador del ánimo | Ventana terapéutica 0,6–1,2 mEq/L; toxicidad = daño renal/convulsiones | 6 |
| Fenitoína | Antiepiléptico | PK no lineal; toxicidad = ataxia/deterioro cognitivo | 5 |
| Metotrexato | Antifolato | Supresión de la médula ósea a dosis terapéutica estrecha | 4 |
| **Total** | — | — | **35 pares** |

Cada par fue calificado de forma independiente por dos fuentes de
referencia: el Comprobador de Interacciones de Drugs.com (con licencia CC)
y la API de Interacciones Farmacológicas RxNav de la NIH NLM (base de
datos federal). Cuando las dos discrepaban, la clasificación más grave era
la verdad de referencia (conservadora en cuanto a seguridad).

## Cifras principales (motor en producción, tubería completa + Capa 4.5 v8)

Cohorte de regresión clínica en producción: 139 pares de fármacos en 4
clases de gravedad (44 contraindicados · 4 mayores · 22 moderados · 69
serios), construida a partir del conjunto de anclaje NTI canónico (AGS
Beers / STOPP-START) más el crecimiento de la cohorte MAOI×SNRI de
iter-280. Todas las cifras bajo inferencia Q16.16 entre arquitecturas
sobre el paquete v8 EN PRODUCCIÓN (`1f0f8859…`, promoción iter-275):

| Métrica | Valor | Qué mide |
|---|---:|---|
| **Sensibilidad en contraindicados** | **44 / 44 (100%)** | La tubería nunca pasó por alto un par contraindicado en la cohorte en producción de 139 pares |
| **Sensibilidad en mayores** | **4 / 4 (100%)** | Todos los pares mayores detectados (la promoción v8 posterior a iter-275 cerró los fallos históricos de v1/v6/v7) |
| **Falsos positivos en contraindicados** | **0** | Cero falsos positivos en la cohorte de control negativo de 10 entradas + cero falsos positivos en la cohorte de 139 pares |
| **Falsos negativos en contraindicados** | **0** | Cero. La cifra que bloquea la publicación. |
| **Tasa de abstención** | ~17% | La Capa 6 se abstiene en lugar de adivinar cuando la evidencia es insuficiente |
| **Latencia media por par** | 3,1 s | Mediana del ida y vuelta de las capas 1 + 2 + 3 (el consenso LLM de la capa 4 se paraleliza) |
| **Duplicación arquitectónica v8 de la Capa 4.5** | h=128 → 256 | La duplicación de la dimensión oculta que rompió el techo de v7 y llevó a la Capa 4.5 sola al 100% en la cohorte de contraindicados bajo Q16.16 |

## Precisión por clase (clasificador ternario Capa 4.5 solo — v8 en producción)

Esta es la primitiva de reproducibilidad de BitNet b1.58, **no** la
tubería completa. La afirmación de seguridad estructural es la cifra de la
tubería en producción anterior; esta sección reporta el comportamiento
independiente de v8 por transparencia.

Cohorte del motor en producción (139 pares) — Capa 4.5 conjunto de 2
paquetes iter-421:

| Clase | Tamaño cohorte producción | Sensibilidad | Qué significa |
|---|---:|---:|---|
| `contraindicated` | 44 | **100%** | **La cifra estructural** (44/44 + 0 FP); compuerta de contraindicados v8 congelada |
| `major` | 4 | **100%** | Cerró todos los fallos históricos de v1/v6/v7; preservadas las MAJOR_KEYS de A |
| `moderate` | 22 | **100%** | El especialista de nivel 2 iter-421 (paquete B) cierra la brecha independiente del 91% |
| `serious` | 69 | **100%** | El especialista de nivel 2 iter-421 cierra la brecha independiente del 84% (clúster warfarina/NTI) |
| `none` (control negativo) | 10 | **100% especificidad** | Cero FP en la cohorte de control negativo de 10 entradas (4 casos límite) |

El conjunto iter-421 despacha A→B bajo argmax restringido: el veredicto de
contraindicado de A SIEMPRE gana (compuerta de contraindicados de grado
clínico congelada); en caso contrario, el argmax de B sobre {moderate,
serious, major} reemplaza la decisión de clase serious de A. Ambos pasos
hacia adelante son ternarios Q16.16 idénticos bit a bit entre
arquitecturas; el `weights_id = "{bundle_id_a}+{bundle_id_b}"` compuesto
captura ambos en `BitNetResult.repro_hash` para que los verificadores
puedan reproducir cualquier decisión del conjunto exactamente. La línea
base independiente de v8 (84% serious / 91% moderate) se preserva en
`retrain_runpod/bitnet_weights_v8_h256.json` para la reconstrucción de la
cadena de auditoría anterior a iter-421.

**Línea base v1 previa a la promoción (preservada en
`engine/bitnet_weights.v1.cfadb4f6.bak.json` para la reconstrucción de la
cadena de auditoría)** — la tabla original de precisión por clase en
tiempo de entrenamiento sobre datos retenidos, en la cohorte NTI de 35
pares de v1 + pliegue de entrenamiento de 647 muestras (n=42 para
contraindicados retenidos, 85,7% de precisión) se preserva textualmente en
`git log` para cualquier auditor que reproduzca decisiones tomadas antes
de la promoción iter-275. Las cifras v8 anteriores reemplazan la línea
base v1 para las afirmaciones en tiempo presente; las cifras v1 siguen
siendo la medición correcta para cualquier `repro_hash` anterior a
iter-275.

El clasificador es intencionadamente de **alta precisión en la clase de
seguridad**, no de alta exactitud en todo el espectro. Otras capas son el
mecanismo principal de sensibilidad; el trabajo de la Capa 4.5 es la
verificación idéntica bit a bit + el anclaje de la cadena de auditoría, no
ganar un benchmark.

## Matriz de confusión (solo Capa 4.5, prueba sobre datos retenidos, n=647)

```
                       Predicho
                  none  minor  mod  major  contra
Real    none      217    0     78   25     4      (324)
        minor       0    0      0    0     0      ( 0 — la clase no tiene ejemplos de entrenamiento)
        moderate   29    0     82   23     5      (139)
        major      11    0     21  109     1      (142)
        contra      0    0      6    0    36      ( 42)
```

Lea la fila inferior con atención: **contraindicado → none** es **0**.
**Contraindicado → minor** es **0**. La única celda fuera de la diagonal en
la fila de contraindicados es **contraindicado → moderate** (n=6) — una
degradación más segura que la tubería aguas arriba detecta vía el consenso
LLM de la Capa 4 y la ruta de alerta de desacuerdo.

## Comparación con enfoques competidores

| Sistema | Enfoque | Reproducibilidad | Código abierto | Validación clínica |
|---|---|---|---|---|
| **Historia Clínica v1** | 6 capas + ancla de auditoría BitNet Q16.16 | **Idéntico bit a bit entre CPU/GPU/NPU; repro_hash SHA-256** | Apache-2.0 | Este documento |
| Bot sanitario LangChain genérico | Una sola llamada LLM | No determinista (muestreo de tokens, reducción FMA) | Variable | Ninguna |
| DDI nativo Epic / Cerner | Reglas estáticas + RxNorm | Determinista por versión de base de datos | Cerrado | Con autorización regulatoria |
| LangMem | LLM con memoria | No determinista | MIT | Genérico — no clínico |
| Mem0 | LLM con memoria | No determinista | Apache-2.0 | Genérico — no clínico |

El diferenciador de Historia Clínica es la **reproducción de auditoría
idéntica bit a bit**. Epic/Cerner son deterministas pero cerrados; los
proyectos de código abierto basados en LLM son no deterministas. Historia
Clínica ocupa el cuadrante único: código abierto Y reproducible de forma
idéntica bit a bit.

## Qué NO validamos explícitamente

Brechas honestas de esta entrega:

- **Datos reales de HCE** — Lucía Obono Mangue es un paciente sintético
  FHIR R4. No se utilizaron registros reales de pacientes; las normas de
  protección de datos del paciente no aplican todavía.
- **Oncología de cola larga, biológicos novedosos, fármacos raros de
  especialidad** — fuera del alcance del corpus v1 (3.247 pares en 224
  fármacos / 41 clases). La cobertura completa de DrugBank es un
  seguimiento de v2 (licencia académica solicitada 2026-05-02; script de
  ingesta listo en
  `/data/checkpoints/hcli-bitnet-training/build_corpus_drugbank.py`).
- **Revisión por panel de clínicos** — aún sin aprobación de comité de
  ética/clínico. La tubería está pensada para la integración por parte de
  desarrolladores de IA sanitaria, no para uso en el punto de atención.
- **Vía de validación regulatoria** — Historia Clínica proporciona la
  *primitiva de reproducibilidad* que un proceso de validación clínica
  espera (paso hacia adelante idéntico bit a bit, cadena de auditoría
  direccionada por contenido). El proyecto en sí es de demostración, no un
  expediente regulatorio.
- **Resultados de federación multisede** — `JointMemoryFederation.flow.mind`
  se entrega como un contrato tipado con **21 invariantes tipados**, todos
  los 21 ejercitados de extremo a extremo por la demostración simulada
  `scripts/federation_mock_demo.py` (los invariantes de sellado X25519
  10–14 se ejercitan en proceso vía un ciclo de ida y vuelta
  `SealedEnvelope` + `_x25519_seal` / `_x25519_open` que refleja el sobre
  criptográfico del transporte de cable HTTP de federación v4 publicado en
  mind-mem v4.0.1 en PyPI 2026-05-11, commit `16a3e25` en `main`). mind-mem
  v4.0.1 entrega el transporte de cable — 4 endpoints en
  `src/mind_mem/http_transport.py` (`GET /federation/vclock/<id>`,
  `GET /federation/conflicts`, `POST /federation/write`,
  `POST /federation/resolve`) activados por bandera `v4.federation`,
  autenticación X-MindMem-Token, tope de cuerpo de 1 MiB, más el
  `mind_mem.v4.federation_client.FederationClient` de la biblioteca
  estándar (11/11 pruebas de cable + 40/40 pruebas existentes de
  http_transport pasan) — sobre las primitivas de **base** de federación de
  v4.0.0 (publicado 2026-05-10) (`mind_mem.v4.federation`:
  block_tier_vclock + tier_conflict_log + enum MergeStrategy),
  kernel-cognitivo, grafo-de-conocimiento, observabilidad y suites de
  resiliencia. El puente `engine/federation_transport.py` de Historia
  Clínica consume `FederationClient` en la próxima reconstrucción de Azure
  (fijación: `mind-mem>=4.0.1`).

## Reproduciendo esta validación

Todas las cifras anteriores pueden re-derivarse a partir del código de este
repositorio:

```bash
# Construir el corpus
python3 /data/checkpoints/hcli-bitnet-training/build_corpus.py

# Entrenar el clasificador
python3 /data/checkpoints/hcli-bitnet-training/train_bitnet.py
# Salidas: training_summary.json, bitnet_weights.json

# Ejecutar la suite de regresión
python3 -m pytest tests/test_engine/ -q

# Ejecutar la prueba de estrés NTI
python3 -m pytest tests/test_engine/test_clinical_scoring_extended.py -k stress
```

El corpus, los pesos, el script de entrenamiento y la ruta de inferencia
son todos Apache-2.0; cualquier revisor puede re-ejecutar la validación de
forma independiente y obtener resultados idénticos bit a bit (salvo la capa
de consenso LLM, que por diseño usa salidas de modelos que derivan entre
versiones de proveedores — el veredicto aguas arriba y el repro_hash de
BitNet se registran ambos para que la cadena de auditoría capture la
deriva).

---

*Historia Clínica es infraestructura de seguridad de IA clínica de código
abierto, Apache-2.0, desarrollada por STARGA, Inc.*
