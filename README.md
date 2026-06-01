<p align="center">
  <h1 align="center">Historia Clínica</h1>
  <p align="center">
    <strong>Memoria clínica nacional offline-first y red federada de seguridad del paciente para Guinea Ecuatorial</strong>
  </p>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licencia-Apache--2.0-blue.svg?style=flat-square" alt="Licencia"></a>
  <a href="docs/ARQUITECTURA_EG.md"><img src="https://img.shields.io/badge/arquitectura-offline--first-2E7D32?style=flat-square" alt="Arquitectura"></a>
  <a href="docs/WHITEPAPER_EG_ES.md"><img src="https://img.shields.io/badge/whitepaper-ES-7C3AED?style=flat-square" alt="Whitepaper ES"></a>
  <a href="docs/WHITEPAPER_EG_EN.md"><img src="https://img.shields.io/badge/whitepaper-EN-F97316?style=flat-square" alt="Whitepaper EN"></a>
</p>

---

## El problema

En zonas con conectividad intermitente o nula, el historial de un paciente se fragmenta
entre consultorios que no se comunican entre sí. Una nueva prescripción puede entrar en
conflicto con un anticoagulante o una alergia registrada en otra sede, sin que el
profesional que atiende llegue a saberlo. Los sistemas que dependen de internet o de la
nube no funcionan donde la red no llega.

**Historia Clínica** lleva la memoria clínica y la seguridad del paciente al punto de atención,
funcionando **sin conexión** y conservando la continuidad del historial entre profesionales
y sedes.

## Qué es

Historia Clínica es una red sanitaria **offline-first** (funciona sin internet). Cada consultorio
recibe un **nodo preconfigurado** que contiene, ya instalado y listo para usar:

| Componente | Función |
|------------|---------|
| **Base de datos cifrada de pacientes** | Historial clínico local, cifrado en reposo, accesible solo desde el nodo. |
| **Motor de seguridad farmacológica** | Detección determinista de interacciones, reactividad cruzada de alergias y contraindicaciones, sin servicios externos. |
| **Inferencia clínica local** | Síntesis y razonamiento clínico ejecutados en el propio nodo; los datos del paciente no salen del consultorio. |
| **Registro de eventos de federación** | Bitácora a prueba de manipulaciones que sostiene la sincronización entre nodos. |
| **Interfaz de revisión** | Web/app servida localmente por el nodo a teléfonos y tabletas de la sala. |

Los teléfonos y tabletas actúan **únicamente como interfaz**. Nunca almacenan la base de
datos maestra: el nodo es la fuente de verdad local.

La red **coexiste** con los sistemas nacionales existentes (SNIS, DHIS2). No los reemplaza:
los complementa aportando seguridad del paciente en el punto de atención.

## Arquitectura

```mermaid
flowchart TD
    C["ENTORNO CENTRAL (sede técnica)<br/>entrena y compila los modelos clínicos<br/>genera los nodos preconfigurados<br/>publica actualizaciones firmadas del motor de seguridad"]

    C -->|"entrega física o ventana de red"| N1
    C -->|"entrega física o ventana de red"| N2
    C -->|"entrega física o ventana de red"| N3

    N1["NODO CLÍNICA<br/>BD cifrada · motor de seguridad<br/>inferencia · log de federación"]
    N2["NODO CLÍNICA<br/>BD cifrada · motor de seguridad<br/>inferencia · log de federación"]
    N3["NODO CLÍNICA<br/>BD cifrada · motor de seguridad<br/>inferencia · log de federación"]

    N1 -->|interfaz| D1["Telefono/tableta<br/>(solo interfaz)"]
    N2 -->|interfaz| D2["Telefono/tableta<br/>(solo interfaz)"]
    N3 -->|interfaz| D3["Telefono/tableta<br/>(solo interfaz)"]

    N1 <-->|"sincronización: asíncrona, firmada, idempotente"| N2
    N2 <-->|"sincronización: asíncrona, firmada, idempotente"| N3
```

El único componente que requiere coordinación entre sedes es la **red de federación**, y
ésta funciona de forma **asíncrona**: los nodos sincronizan eventos cuando hay una ventana
de conectividad (horas, días, o mediante transporte físico de datos). Entre
sincronizaciones, cada nodo es plenamente funcional por sí solo.

## Perfiles de dispositivo

```mermaid
flowchart LR
    subgraph A["Perfil A — consultorio rural"]
        A1["Hardware de bajo consumo<br/>(clase Raspberry Pi Zero 2 W)"]
        A2["Batería / panel solar opcional"]
        A3["Historial de la población local"]
        A4["Inferencia clínica ligera"]
    end

    subgraph B["Perfil B — centro comarcal"]
        B1["Hardware de mayor capacidad<br/>(clase Raspberry Pi 4/5)"]
        B2["Más almacenamiento e inferencia"]
        B3["Agrega varios nodos Perfil A"]
        B4["Ventana de sincronización más amplia"]
    end

    A -->|"derivación con continuidad del historial"| B
```

Ambos perfiles ejecutan el **mismo software**. La diferencia es de capacidad, no de
funcionalidad. Un paciente atendido en un consultorio rural y derivado a un centro comarcal
conserva la continuidad de su historial a través de la federación.

## Red de federación

```mermaid
flowchart LR
    N1["Nodo A"] -->|"evento firmado"| L1["Log de federación"]
    N2["Nodo B"] -->|"evento firmado"| L2["Log de federación"]
    L1 <-->|"sincronización asíncrona"| L2
    L1 -.->|"transporte físico si no hay red"| L2
```

- **Asíncrona:** los nodos no necesitan estar conectados simultáneamente.
- **Firmada:** cada evento lleva una firma que verifica su origen e integridad.
- **Idempotente:** reaplicar un evento ya recibido no altera el estado — la sincronización es
  segura aunque se repita o llegue en desorden.
- **Resistente al transporte físico:** sin red, los eventos pueden trasladarse en un soporte
  físico entre sedes sin pérdida de garantías.

## Privacidad y soberanía de los datos

- Los datos del paciente **no salen del consultorio** durante la atención.
- **No hay nube** que concentre los historiales nacionales.
- La sede central solo distribuye software (modelos y motor de seguridad); nunca recibe datos
  de pacientes para operar.
- El cifrado en reposo y las firmas de federación protegen frente a pérdida o manipulación del
  hardware.

## Coexistencia con SNIS / DHIS2

Historia Clínica **no reemplaza** el sistema nacional de información sanitaria. Se sitúa en el
punto de atención y aporta:

- seguridad del paciente en tiempo real (alertas de fármacos, alergias, contradicciones),
- continuidad del historial entre profesionales,
- funcionamiento garantizado sin conexión.

Los indicadores agregados que requieran SNIS/DHIS2 pueden derivarse de los nodos mediante
exportaciones controladas, respetando los flujos de reporte ya establecidos en el país.

## Modelo de despliegue

```mermaid
flowchart TD
    P1["1 · La sede técnica entrena y compila<br/>los modelos y el motor de seguridad"]
    P2["2 · Se genera una imagen preconfigurada<br/>del nodo, lista para usar"]
    P3["3 · Los nodos se entregan físicamente<br/>a cada consultorio o centro"]
    P4["4 · El personal enciende el nodo y conecta<br/>un teléfono o tableta a la interfaz local"]
    P5["5 · Las actualizaciones firmadas se distribuyen<br/>en las ventanas de sincronización"]

    P1 --> P2 --> P3 --> P4 --> P5
```

Sin instalación compleja en sede. Sin dependencia de internet para operar.

## Documentación

| Documento | Contenido |
|-----------|-----------|
| [Arquitectura nacional](docs/ARQUITECTURA_EG.md) | Diseño completo: nodos, perfiles, federación, soberanía de datos, coexistencia SNIS/DHIS2. |
| [Whitepaper (Español)](docs/WHITEPAPER_EG_ES.md) | Documento de revisión para evaluación nacional. |
| [Whitepaper (English)](docs/WHITEPAPER_EG_EN.md) | Review document, English copy. |
| [Demostración](docs/demo.html) | Panel de demostración interactivo (revisión privada). |

## Licencia

Apache-2.0 — consulte [LICENSE](LICENSE) y [NOTICE](NOTICE)

Desarrollado por [STARGA Inc.](https://star.ga)
