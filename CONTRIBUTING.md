# Contribuir a Historia Clínica

Historia Clínica está desarrollado por [STARGA Inc.](https://star.ga)

Este repositorio se encuentra en **revisión privada** para evaluación nacional. La
documentación de arquitectura y los whitepapers son la referencia principal del proyecto.

## Documentación de referencia

| Documento | Contenido |
|-----------|-----------|
| [Arquitectura nacional](docs/ARQUITECTURA_EG.md) | Diseño completo del sistema. |
| [Whitepaper (Español)](docs/WHITEPAPER_EG_ES.md) | Documento de revisión. |
| [Whitepaper (English)](docs/WHITEPAPER_EG_EN.md) | Review document. |

## Principios de diseño

Toda contribución debe respetar los principios fundacionales del proyecto:

- **Offline-first:** el sistema debe operar sin conexión a internet. La red de federación es
  asíncrona y opcional para la atención.
- **Soberanía de los datos:** los datos del paciente no salen del consultorio. No hay nube
  centralizada.
- **El nodo es la fuente de verdad:** teléfonos y tabletas son solo interfaz.
- **Coexistencia:** el sistema complementa SNIS/DHIS2, no los reemplaza.
- **Determinismo y auditabilidad:** la seguridad farmacológica y el registro de federación
  deben ser deterministas y a prueba de manipulaciones.

## Normas de documentación

- Todo el contenido en **español** (con copia en inglés solo para los whitepapers).
- Los diagramas se realizan con **Mermaid**, no con arte ASCII.
- Sin contenido de marketing ni atribuciones externas en los artefactos públicos.
