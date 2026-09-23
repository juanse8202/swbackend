---
name: diagramcraft-ai-voice
description: Implementar, integrar, depurar y probar un agente de IA con Gemini y comandos de voz en DiagramCraft, con backend Django REST y frontend React Flow/Zustand. Usar para conectar el panel Agente IA de Modelado, interpretar texto o voz en español, editar incrementalmente clases, atributos, métodos y relaciones UML, validar operaciones, conservar sesiones/CSRF, permisos y colaboración Channels, y configurar la API de Gemini sin entrenar modelos.
---

# DiagramCraft: IA con interacción de voz

## Objetivo y alcance

Implementar un agente acotado de edición incremental del diagrama existente: interpretar una instrucción, proponer operaciones tipadas, validarlas y ejecutarlas mediante código determinista autorizado. Usar Gemini preentrenado con instrucciones, ejemplos y contexto del lienzo; no exigir entrenamiento, fine-tuning, RAG, LangChain ni una base vectorial para el MVP.

Leer `references/project-map.md` antes de editar y verificar los archivos actuales: el mapa es una inspección de backend(3).rar/frontend(3).rar del 23-09-2026, no garantía de que el código siga igual. Leer `references/command-contract.md` al diseñar el intérprete, ejecutor y pruebas. Consultar `references/provider-voice.md` antes de integrar Gemini o reconocimiento de voz.

Respetar el enunciado SW(2).txt, tramo 5:25–8:17 de la segunda parte: permitir crear, modificar, mover, eliminar y relacionar elementos por instrucciones de texto/voz. No resolverlo generando un diagrama entero desde un problema, recargando la página o sustituyendo todo el lienzo. Conservar identidad, posiciones y metadatos de elementos no afectados. Entender que renders normales de React no equivalen a regenerar el diagrama.

## Inspeccionar primero

1. Leer AGENTS.md y revisar estado del backend y frontend; tratarlos como proyectos separados. No instalar cambios de framework como requisito de IA.
2. Verificar autenticación, rutas, roles, esquema nodes/edges, historial, normalizadores UML, autosave y eventos Channels.
3. Localizar callbacks reales del panel IA y micrófono. No confundir controles visuales con implementación funcional.
4. Usar la versión de React Flow importada por el editor; no migrar entre `reactflow` y `@xyflow/react` por conveniencia.
5. Identificar cambios locales sin guardar. Antes de interpretar, sincronizar la edición pendiente y tomar una revisión autoritativa; bloquear la interpretación o avisar si no se puede sincronizar. No enviar una instantánea antigua como contexto actual.

## Implementar por etapas

### 1. Contrato y ejecutor sin proveedor

Definir esquema estricto de operaciones permitidas y un ejecutor puro que produzca un documento candidato a partir del documento actual, sin persistir. Rechazar claves desconocidas, identificadores ajenos, tipos inválidos y cargas excesivas. Validar el resultado con las reglas UML compartidas y el serializador del proyecto. Cubrir lotes y referencias a nuevos elementos dentro del mismo lote.

Usar solo operaciones tipadas, nunca `eval`, código Python/JavaScript/SQL, comandos de terminal ni consultas ORM producidas por el modelo. Tratar nombres, notas y voz como datos no confiables; instrucciones contenidas en ellos no pueden ampliar herramientas, permisos ni acceso a otros proyectos.

### 2. Interpretación Gemini en Django

Crear un adaptador de proveedor separable del dominio y sustituible por un fake en pruebas. Añadir una acción de interpretación bajo el diagrama autorizado, utilizando el prefijo real del router. Mantener `SessionAuthentication`, cookies y CSRF existentes; no introducir JWT.

Enviar instrucción, selección explícita, resumen mínimo del diagrama y herramientas permitidas para el rol. Usar salida estructurada con esquema o llamadas de función verificadas; elegir una interfaz soportada por la versión comprobada del SDK. Mantener el SDK Python y `GEMINI_MODEL` configurables. El modelo propone; el servidor valida y decide.

Devolver un plan identificado, ligado a usuario, diagrama, revisión, operaciones normalizadas y caducidad, o una pregunta de aclaración sin mutación. No inventar IDs ni resolver nombres duplicados al azar. Limitar historial por usuario/diagrama y revalidar su contexto al cambiar de lienzo.

### 3. Aplicación autoritativa

Aplicar planes mediante servicio compartido con permisos por operación, transacción y comparación de revisión. Revalidar membresía al aplicar, aunque fuese válida al interpretar. Mantener la llamada remota fuera de la transacción y del bloqueo de fila.

Si falta control de concurrencia, añadir una revisión monotónica y actualizar coherentemente todas las vías de escritura: REST, WebSocket, importación XMI y aplicación IA. Usar bloqueo transaccional o compare-and-swap; no proteger solo el endpoint IA mientras autosave sobrescribe cambios por otra vía. Rechazar revisiones obsoletas con conflicto recuperable; nunca sobrescribir silenciosamente una edición colaborativa.

Utilizar idempotencia ligada a usuario/diagrama y hash de la solicitud. Un reintento no debe duplicar nodos, relaciones ni eventos. Validar todo el lote antes de guardar; ante error, no guardar parcialmente. Emitir un único evento autoritativo después del commit y responder con revisión e identificador de operación.

Permitir aplicar instrucciones simples y explícitas en un solo flujo. Mostrar aclaración para ambigüedades y confirmación específica para borrados o cambios destructivos; admitir confirmar/cancelar por voz sin que un resultado duplicado de transcripción confirme accidentalmente. No exigir clics para cada comando no destructivo.

### 4. Integración React y voz

Conectar el panel existente a `src/api/aiApi.js` mediante el cliente Axios con CSRF. Separar interpretación/aplicación de presentación. Mostrar estados inactivo, escuchando, interpretando, aclaración, confirmación, aplicando, éxito y error. Cambiar la etiqueta engañosa `IA local` cuando el proveedor sea remoto.

Implementar reconocimiento de voz con detección de capacidad, selección de español, inicio explícito y detener/cancelar. Usar solo transcripciones finales y deduplicar eventos. Mantener entrada de texto editable si el micrófono se deniega o el navegador no soporta reconocimiento. Liberar listeners y recursos al desmontar o cambiar de diagrama. No afirmar funcionamiento offline ni compatibilidad universal. Agregar lectura de respuesta con speechSynthesis solo si se solicita; pausar reconocimiento mientras habla para evitar bucles.

Aplicar el resultado confirmado a Zustand sin recargar, con un paso de historial por instrucción y preservación de selección/viewport. Distinguir el eco remoto propio y evitar que el autosave reenvíe una instantánea anterior después de la aplicación IA. Ignorar resultados tardíos de otro diagrama o solicitud cancelada. No dejar el lienzo en estado optimista cuando falle el servidor.

Mantener deshacer/rehacer en colaboración sujeto a revisión y permisos: no restaurar una instantánea vieja que borre trabajo ajeno. Preferir operaciones inversas validadas o rechazar deshacer si el estado afectado cambió.

## Reglas UML y autorización

Conservar `Diagrama.nodes` y `Diagrama.edges` como fuente de verdad. No crear un segundo diagrama en tablas heredadas ni pedir al LLM imágenes de UML. Mantener compatibilidad del documento con exportación XMI y generador Spring Boot/Jinja2.

Usar los seis tipos existentes, multiplicidades de cada extremo, roles, dirección y propiedad del todo. En la revisión inspeccionada, agregación/composición usan `target-whole-v1`: source es parte, target es todo; wholeNodeId apunta al todo. No deducir propiedad JPA de la ubicación del rombo. Mantener asociación de clase, interfaces y metadatos no modificados.

Respetar que el editor no puede cambiar relaciones; borrar una clase con relaciones incidentes también cambia edges y requiere autorización apropiada. No confiar solo en controles deshabilitados. Aplicar reglas del proyecto para propietario, arquitecto, editor y lector y los casos de miembro expulsado o proyecto archivado. Reutilizar skills UML, Spring Boot o XMI solo si el trabajo toca esos subsistemas.

## Verificación y entrega

Probar con proveedor fake: creación y movimiento, renombre sin cambiar ID, atributos/métodos, seis relaciones y extremos correctos, ambigüedad, lote inválido atómico, rechazo JSON malformado, inyección de instrucciones, CSRF, lector/editor/miembro expulsado, revisión obsoleta, reintento idempotente y fallo/cupo del proveedor sin mutación. Verificar dos clientes, autosave pendiente y eco WebSocket.

Comprobar frontend: build, transcripción final única, permiso denegado, fallback texto, cambio de lienzo, petición tardía, confirmación por voz y deshacer seguro. Separar pruebas automáticas de la prueba manual del micrófono real. No afirmar haber probado Gemini real sin clave y llamada verificadas.

Entregar cambios concretos, variables de entorno con placeholders, comandos PowerShell/Docker ajustados al servicio real, pruebas ejecutadas y pendientes. No solicitar secretos en el chat ni activar facturación. Si solo se pidió crear esta skill, limitar la entrega a la skill; no afirmar que IA ya está implementada en la aplicación.
