---
name: diagramcraft-xmi-interoperability
description: Implement, extend, debug, or review bidirectional XMI interoperability between DiagramCraft and Enterprise Architect across the React/React Flow frontend and Django REST backend. Use for secure XMI upload and parsing, XMI export and download, Enterprise Architect dialects, mapping UML packages/classes/interfaces/attributes/operations and six relationship types to Diagrama.nodes/Diagrama.edges JSON, stable IDs, multiplicities and diamond ownership, round-trip preservation, validation, compatibility reports, and frontend/backend tests.
---

# DiagramCraft XMI Interoperability

Implement real editable-model interchange between DiagramCraft and Enterprise Architect (EA). Keep `Diagrama.nodes` and `Diagrama.edges` as DiagramCraft's authoritative persistence. Treat XMI as an import/export format, not as a second database.

## Inspect the two repositories first

1. Locate the separate React and Django repositories and read their repository instructions.
2. Inspect the React Flow node/edge shape, Zustand stores, autosave, API modules, editor toolbar, dialogs, notifications, permissions, and tests.
3. Inspect the Django `Diagrama` model, serializer, viewset, routes, permissions, JSON validation, collaboration behavior, and tests.
4. Trace one class and one relationship through React state, the API, and `Diagrama.nodes`/`Diagrama.edges` before editing the contract.
5. Search for existing XMI, XML, import, export, upload, or download code. Extend an existing path instead of creating a competing one.
6. Preserve unrelated changes and current diagram behavior.

## Preserve one source of truth

Use this import flow:

`XMI upload -> secure Django parser -> detected dialect/version -> normalized in-memory UML model -> validation/report -> Diagrama.nodes/Diagrama.edges -> React Flow`

Use this export flow:

`Diagrama.nodes/Diagrama.edges -> normalized in-memory UML model -> validation -> EA-compatible XMI serializer -> XMI download`

Do not read or write legacy `ClaseUML`, `AtributoUML`, or `RelacionUML` tables during normal import/export unless the repository has an explicit active compatibility requirement. Do not persist the normalized model; it exists only in memory.

Never treat PNG, JPG, PDF, screenshots, or copied diagram images as editable XMI.

## Define an intermediate UML model

Normalize both XMI and DiagramCraft JSON into the same typed in-memory representation. Include only semantic data needed for round trips:

- Model and package hierarchy.
- Stable external XMI ID and DiagramCraft ID mappings.
- Class or interface kind, name, visibility, abstract flag, stereotypes, documentation, and package.
- Attributes: name, visibility, type, multiplicity, default value, static flag, and derived flag when supported.
- Operations: name, visibility, parameters, return type, abstract/static flags, and documentation when supported.
- Relationships: type, source, target, semantic endpoint roles, multiplicities, navigability, association-end names, aggregation kind, and documentation.
- Diagram layout only when the selected EA dialect supplies supported coordinates; otherwise apply a deterministic layout after import.

Keep React Flow-only state such as selection, viewport, transient handles, and UI callbacks out of the intermediate model.

Maintain an explicit ID map. Never assume an XMI identifier is a safe React Flow ID, Java identifier, filename, or database key.

## Support XMI dialects deliberately

Detect the XMI version, UML namespace, exporter name, exporter version, and EA extension blocks before mapping content. Do not claim compatibility merely because the document is valid XML.

- Prefer a small adapter per supported dialect/version behind a common normalized model.
- Reject unsupported versions with a structured compatibility error.
- Preserve unknown extensions in a controlled metadata field only when round-trip preservation is required and safe.
- Do not silently discard unsupported semantic elements. Report imported, skipped, downgraded, and rejected items.
- Use namespaces, element kinds, and references; do not parse by fragile prefix strings alone.

When the project has no chosen baseline, implement and test one real EA-exported fixture first, then add other dialect adapters explicitly. Do not invent an EA schema from memory when a representative export is available.

## Map UML elements

At minimum, import and export:

- Packages.
- Classes.
- Interfaces.
- Attributes and supported primitive/custom types.
- Operations and parameters when present in DiagramCraft's current schema.
- Association.
- Aggregation.
- Composition.
- Generalization/inheritance.
- Interface realization.
- Dependency.
- Multiplicities and navigability.

Keep stable lowercase DiagramCraft machine values consistent with the current editor contract. Add a compatibility mapper for legacy values; never reinterpret values implicitly.

## Preserve relationship semantics

### Association

Map UML association ends explicitly. Preserve end IDs, role names, types, multiplicities, and navigability. Do not infer semantics only from XML element order.

### Aggregation and composition

Read the aggregation property from the association end and identify the whole end explicitly:

- Shared aggregation -> open diamond.
- Composite aggregation -> filled diamond.

Store a semantic whole/part endpoint in `Diagrama.edges`. Do not use drag direction, `source`, XML order, or SVG marker position as the sole definition of ownership.

On export, place the aggregation/composition marker at the same semantic whole end that was imported or authored.

### Inheritance

Map UML generalization to DiagramCraft inheritance with subclass as source and superclass as target according to the project's canonical edge contract. Validate self-inheritance, cycles, and Java-incompatible multiple class inheritance when relevant to downstream generation.

### Realization

Map interface realization with implementing class as source and interface as target. Validate that the target is an interface. Preserve the dashed line and hollow triangle rendering contract.

### Dependency

Preserve client and supplier orientation. Do not convert a dependency to an association or database relation.

## Parse XML securely in Django

Treat every upload as untrusted.

- Enforce authentication, project membership, diagram permissions, file extension, content type as a hint, maximum byte size, maximum element/depth/count limits, and request timeouts.
- Use a hardened XML parser that disables external entities, DTD resolution, network access, and entity expansion. Prevent XXE and exponential entity attacks.
- Do not execute scripts, evaluate expressions, dereference remote schemas, or fetch external URLs from XMI.
- Reject malformed XML and duplicate/conflicting IDs with structured errors.
- Avoid logging entire uploads or sensitive model documentation.
- Parse in memory or an isolated temporary file and always clean it up.
- Protect import from excessive nodes, edges, strings, nesting, and decompression bombs if archived formats are later supported.

Do not modify the diagram until the entire import has parsed and validated successfully. Apply the resulting `nodes` and `edges` atomically. If merge import is supported, define conflict rules; otherwise require replace or create-new behavior explicitly.

## Implement Django endpoints

Follow existing routing conventions. Typical operations are:

- `POST /api/diagramas/{id}/importar-xmi/`
- `GET` or `POST /api/diagramas/{id}/exportar-xmi/`

The import endpoint must:

1. Verify access before parsing.
2. Accept one bounded XMI file and explicit import mode.
3. Detect dialect/version and normalize the complete document.
4. Return a dry-run preview/report when supported.
5. Apply valid `nodes` and `edges` atomically.
6. Return counts, warnings, skipped elements, ID mappings, and actionable errors.

The export endpoint must:

1. Verify access.
2. Load `Diagrama.nodes` and `Diagrama.edges`.
3. Normalize and validate before serialization.
4. Select only a supported target EA/XMI dialect.
5. Return XML using an XMI filename and an appropriate content type.
6. Avoid embedding credentials, internal database IDs, or private server paths.

## Implement the React frontend

Add import/export controls consistent with the existing editor design; do not build a parallel editor.

### Import XMI

- Add an `Importar XMI (EA)` action and file picker accepting `.xmi` and supported XML forms.
- Display the chosen filename, size, target diagram, and replace/create/merge mode before mutation.
- Warn clearly when replace mode will overwrite current nodes and edges.
- Show uploading/parsing progress and prevent duplicate submissions.
- Display structured compatibility results: detected version/exporter, imported counts, warnings, skipped items, and errors.
- Refresh the editor from the authoritative Django response after success; do not reconstruct a separate client-only result.
- Fit the imported graph to view and use deterministic layout only when coordinates are unavailable.
- Preserve autosave/WebSocket behavior without sending stale pre-import state over the successful import.

### Export XMI

- Add an `Exportar XMI (EA)` action.
- Offer only supported target dialects/versions.
- Request the file through the shared authenticated Axios client as binary/text data as appropriate.
- Use a safe server-provided filename, trigger one browser download, and revoke temporary object URLs.
- Surface validation errors and select the implicated node or edge when the backend returns its ID.

### State and contract

- Centralize XMI version and relationship enums.
- Keep backend IDs, XMI IDs, and React Flow IDs distinct.
- Do not trust frontend-only validation; Django remains authoritative.
- Prevent import/export actions for read-only users using both UI state and backend permission checks.

## Validate interoperability

Before committing an import or producing an export, validate:

- Supported XMI and UML namespaces/dialect.
- Unique IDs and resolvable references.
- Legal package containment without cycles.
- Unique or deliberately scoped model names according to project rules.
- Valid class/interface kinds.
- Valid attribute/operation types and multiplicities.
- Existing source/target endpoints for every relationship.
- Correct aggregation/composition whole end.
- Realization target is an interface.
- Inheritance target is a class and has no cycles.
- Valid DiagramCraft JSON shape after mapping.

Return errors with an error code, message, XMI element ID when known, DiagramCraft element ID when known, and suggested correction. Keep warnings distinct from blocking errors.

## Test with real fixtures

Use anonymized XMI files actually exported by each claimed EA/version combination. Keep small focused fixtures for parser unit tests and at least one representative end-to-end fixture.

Test at minimum:

- Secure rejection of DTDs, external entities, malformed XML, oversized input, deep nesting, and duplicate IDs.
- Packages, classes, interfaces, attributes, operations, and custom types.
- All six relationship types.
- Source/target multiplicities, navigability, roles, and diamond ownership on either endpoint.
- Layout present and layout absent.
- Unsupported elements produce warnings rather than silent loss.
- Import permissions, export permissions, and atomic rollback.
- React upload, confirmation, progress, report, refreshed canvas, and download behavior.
- Export from DiagramCraft -> open/import in EA -> export from EA -> reimport into DiagramCraft.
- Semantic comparison before and after the round trip, ignoring allowed layout or exporter-metadata differences.
- Repeated export is deterministic for the same diagram and target dialect.

Do not declare EA compatibility solely from XML snapshots. When EA is unavailable in the environment, state that automated structural tests passed but manual EA round-trip verification remains pending.

## Completion criteria

Consider the feature complete only when:

- `Diagrama.nodes` and `Diagrama.edges` remain the single persistence authority.
- Frontend users can securely import and export XMI according to their permissions.
- Backend parsing and serialization use a normalized in-memory model.
- Classes, interfaces, supported members, packages, and all six relationships preserve semantics.
- Aggregation/composition diamonds remain on the correct whole endpoint.
- Unsupported content is reported rather than silently discarded.
- Security, permission, parser, serializer, frontend, and round-trip tests pass.
- A real EA round trip is verified for every claimed compatible dialect/version, or the unverified status is stated explicitly.
