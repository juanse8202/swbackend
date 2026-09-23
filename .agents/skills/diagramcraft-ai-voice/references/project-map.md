# Mapa verificado del proyecto

Inspección: backend(3).rar, frontend(3).rar y SW(2).txt, 23-09-2026. Volver a leer código vigente antes de modificarlo.

## Backend

- `config/authentication.py`: LoginView/LogoutView/CurrentUserView y backend correo o usuario. Autenticación de sesión; conservar CSRF.
- `diagramas/models.py`: Diagrama y JSON nodes/edges; también modelos UML heredados.
- `diagramas/serializers.py`: DiagramaSerializer valida el documento, clases y relaciones. Reutilizarlo, sin reducir semántica al formato JSON.
- `diagramas/views.py`: DiagramaViewSet, acceso por miembros, permiso de edición y restricción de edges para editor; acciones generar-spring-boot/importar-xmi/exportar-xmi.
- `proyectos/permissions.py`: role_for, require_role y EDIT_ROLES.
- `diagramas/consumers.py`: save_diagram_if_user_can_access guarda snapshots; en la revisión leída no pasa por DiagramaSerializer y no compara una revisión. Unificar validación y concurrencia al implementar el servicio común, sin suponer que ya existen.
- `diagramas/spring_generator.py` y `diagramas/xmi.py`: preservar compatibilidad, no reescribir para integrar IA.
- `requirements.txt`, `config/settings.py`, `docker-compose.yml`: incorporar SDK y variables backend siguiendo prácticas presentes; nunca copiar valores de .env a la skill.

## Frontend

- `src/components/editor/EditorToolbar.jsx`: panel Agente IA de Modelado, entrada command, botón Ejecutar instrucción, FiMic y etiqueta IA local.
- `src/components/editor/DiagramCanvas.jsx`: pasa `onCommand={() => {}}` y `onListen={() => {}}`. Son callbacks vacíos, no un intérprete local. Contiene autosave, integración de store, permisos y gestión del lienzo; inspeccionar su flujo completo.
- `src/stores/diagramStore.js`: Zustand, makeNode, normalizeRelationEdge, history/future y listener de mutaciones.
- `src/hooks/useDiagramSocket.js`: conexión y cola de evento pendiente; cuidar reenvío después de aplicar IA.
- `src/api/axios.js`: CSRF y credenciales; reutilizar.
- `src/api/diagramApi.js`: rutas relativas `/diagramas/diagramas/`; no asumir `/diagramas/{id}`.
- No existían `src/api/aiApi.js` ni componente de voz en los archivos inspeccionados; son destinos propuestos, no archivos actuales.
- package.json contiene reactflow 11 y @xyflow/react 12, pero store/editor importan `reactflow`. Mantener consistencia con el código activo.

## Documento del lienzo

Nodo: `{id, type: 'umlClass', position: {x,y}, data: {...}}`.
Campos reales: data.title (suele acabar en .java), kind, properties, methods, abstract, persistent, literals, apariencia y otros metadatos. properties incluye visibility/name/type/id/embedded. Inspeccionar forma actual de methods antes de generar parches.

Tipos: class, entity, dto, enum, embeddable, interface. Una entidad nueva usa PK UUID por defecto; utilizar normalizadores del proyecto para no duplicarla. No agregar properties a interface/enum cuando las reglas actuales lo prohíben.

Arista: `{id, source, target, type: 'relationEdge', data: {...}}`.
Campos: relationType, multiplicidadOrigen, multiplicidadDestino, relationConvention, wholeNodeId, ownerNodeId, jpaAnnotation, umlLabel, sourceRole, targetRole, bidirectional y posible associationClassNodeId. Conservar handles y anclajes cuando no cambien.

Tipos: asociacion, agregacion, composicion, herencia, realizacion, dependencia.
El store usa source parte -> target todo en agregación/composición, con relationConvention `target-whole-v1`. En herencia source es subtipo y target supertipo; realización clase -> interfaz. Validar contra las reglas actuales.

El serializador inspeccionado permite multiplicidades N, *, 1, 0..1, 0..*, 1..*. Si se requieren otras, extender coherentemente validación y pruebas, no introducirlas solo desde IA.
