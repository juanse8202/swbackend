# DiagramCraft frontend relationship context

## Current structure

The uploaded frontend is React 19 + Vite + Zustand. It has both `reactflow` v11 and `@xyflow/react` v12 installed, but the current editor imports `reactflow`. Relevant paths:

- `src/components/editor/DiagramCanvas.jsx`
- `src/components/editor/EditorToolbar.jsx`
- `src/components/editor/RelationEdge.jsx`
- `src/stores/diagramStore.js`
- `src/api/diagramApi.js`
- `src/hooks/useDiagramSocket.js`

Keep one React Flow implementation within a change. Do not mix imports, marker types, hooks, or edge APIs between packages.

## Existing flow

`EditorToolbar` currently offers JPA options: `oneToMany`, `manyToOne`, `manyToMany`, and `oneToOne`.

`DiagramCanvas` already:

- registers `RelationEdge` as `relationEdge`;
- opens `RelationDialog` from the toolbar;
- delegates creation to `diagramStore.createRelation()`;
- handles direct canvas connections through `diagramStore.onConnect()`;
- autosaves the complete `nodes` and `edges` arrays;
- broadcasts complete document changes over WebSocket;
- restricts relation use to owner/architect.

`diagramStore` currently stores relation data such as:

```js
{
  label: '@OneToMany',
  umlLabel: '',
  cardinality: '1 : N',
  multiplicidadOrigen: '1',
  multiplicidadDestino: 'N'
}
```

`RelationEdge` renders one dashed Bézier line and edits the UML label and multiplicities. It does not yet vary notation by UML relationship type.

## Target edge schema

Extend existing edges rather than replacing the full document format. A useful compatible schema is:

```js
{
  id: 'rel-<uuid>',
  source: '<node-id>',
  target: '<node-id>',
  type: 'relationEdge',
  data: {
    relationType: 'asociacion',
    umlLabel: 'realiza',
    multiplicidadOrigen: '1',
    multiplicidadDestino: '0..*',
    jpaAnnotation: '@OneToMany'
  }
}
```

Keep a compatibility mapper for old edges that only have `label`, `cardinality`, or JPA types. Do not destroy diagrams saved before the change.

## UI changes

Extend the existing relation section and dialog to select:

- relationship type;
- source node;
- target node;
- label;
- source and target multiplicities;
- optional derived JPA annotation.

Direct drag-to-connect should use the currently selected relationship type or open the existing dialog to complete the metadata. It must not always create `@OneToMany`.

## SVG notation

Use one `RelationEdge` component with configuration by `data.relationType`:

| Type | Stroke | Source marker | Target marker |
| --- | --- | --- | --- |
| asociación | solid | none | optional open arrow |
| agregación | solid | hollow diamond at whole | none |
| composición | solid | filled diamond at whole | none |
| herencia | solid | none | hollow triangle at parent |
| realización | dashed | none | hollow triangle at interface |
| dependencia | dashed | none | open arrow |

Define SVG markers with unique IDs safe for multiple edge instances. Set `markerStart` or `markerEnd` according to source/target semantics. Keep labels and multiplicities in `EdgeLabelRenderer`.

## Store behavior

- Generate IDs with `crypto.randomUUID()` when available, with a collision-resistant fallback.
- Store `relationType` on every new edge.
- Preserve the mutation listener so edits trigger autosave.
- Add update/delete operations for relation metadata without bypassing the listener.
- Normalize legacy edges during `restore()` or rendering, without repeatedly mutating saved state on every render.
- Ensure deleting a node continues deleting its attached edges.

## API decision

The current `diagramApi.guardarDiagrama()` patches complete JSON arrays. Do not add `relationApi.js` unless normalized `RelacionUML` becomes an intentional persistence authority. If it does, use the existing base URL convention: Axios is rooted at `<host>/api`, so the relation path is `/diagramas/relaciones/`.

## Frontend verification

From the frontend root:

```powershell
npm run lint
npm run build
```

Manually verify each notation, multiplicity editor, reload persistence, legacy-diagram compatibility, relation deletion, role restrictions, and WebSocket synchronization.
