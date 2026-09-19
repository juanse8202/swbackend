---
name: diagramcraft-uml-relations
description: Implement, integrate, debug, or extend UML class relationships in the DiagramCraft Django REST backend and React Flow/Zustand frontend. Use for association, aggregation, composition, inheritance, realization, dependency, multiplicities, edge rendering, persistence, permissions, and backend/frontend synchronization in this specific project.
---

# DiagramCraft UML Relations

Work from the project's existing relationship implementation instead of adding a parallel generic example.

## Establish the current checkout

Locate the backend and frontend roots before editing. Confirm the relevant files still match the architecture described in the references; repository code is authoritative when it differs.

- Read [references/backend.md](references/backend.md) before backend model, serializer, endpoint, permission, migration, or test work.
- Read [references/frontend.md](references/frontend.md) before React Flow, Zustand, relation dialog, custom edge, API, or persistence work.
- Read both references for any end-to-end relationship change.

Do not assume `@xyflow/react` APIs: the current editor imports from `reactflow` v11 even though both packages are installed. Preserve that choice unless the user explicitly requests a migration.

## Choose one persistence authority

The project currently has two representations:

1. `Diagrama.nodes` and `Diagrama.edges` JSON, which the editor actively saves and synchronizes.
2. Normalized `ClaseUML` and `RelacionUML` tables, whose REST endpoints exist but are not used by the current canvas.

Before implementing backend synchronization, determine which representation is authoritative. If the user has not chosen, prefer JSON as the immediate authority because autosave and WebSocket collaboration already depend on it. Do not silently write both representations: partial dual writes create drift. If normalized records are required for code generation or queries, implement an explicit transactional synchronization boundary and tests.

## Relationship vocabulary

Support these UML values consistently across backend and frontend:

- `asociacion`
- `agregacion`
- `composicion`
- `herencia`
- `realizacion`
- `dependencia`

Use stable lowercase machine values and Spanish display labels. Migrate legacy Django values such as `Asociacion` and legacy JPA edge types such as `oneToMany` deliberately; never reinterpret stored values without a data migration or compatibility mapper.

Keep UML semantics separate from JPA cardinality. Association, aggregation, composition, inheritance, realization, and dependency define the line/endpoint notation. `1`, `0..1`, `0..*`, and `1..*` define multiplicity. `@OneToMany` and similar annotations are derived JPA metadata, not UML relationship types.

## Implementation requirements

- Preserve project membership and role rules. Only owner/architect may modify relationships in a project.
- Validate that source, target, and relationship all belong to the same diagram.
- Allow self-relations only when the requested domain needs them; otherwise reject them in both UI and API validation.
- Give every edge a stable ID. Do not use `Date.now()` alone when concurrent users can create edges.
- Render UML endpoints accurately with SVG markers: open triangle for inheritance/realization, hollow diamond for aggregation, filled diamond for composition, and suitable solid/dashed strokes for the other types.
- Persist type, source, target, label, source multiplicity, and target multiplicity.
- Preserve autosave, WebSocket updates, restored selections, and read-only behavior.
- Avoid adding a second relation selector when the existing toolbar and `RelationDialog` can be extended.

## Verification

Run the smallest relevant checks available in the checkout:

- Backend: Django checks, migration consistency, and focused API/serializer tests.
- Frontend: lint/build and focused store or component tests when configured.
- End-to-end invariants: create, reload, edit multiplicities, delete, permission denial, and collaborative refresh retain the same relation type and notation.

Report any architectural choice between JSON-only, normalized-only, or synchronized persistence because it changes later AI and Spring Boot generation work.
