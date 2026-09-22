---
name: diagramcraft-generate-spring-boot
description: Implement, extend, debug, or review DiagramCraft end to end across its React/React Flow frontend and Django REST backend so users can model UML and export downloadable Java Spring Boot projects. Use for editor nodes and edges, Diagrama.nodes/Diagrama.edges JSON persistence, class/interface properties, six UML relationship types, multiplicities, frontend API state and ZIP download, in-memory UML normalization, Django validation, UML-to-Java/JPA mapping, Jinja2 templates, ZIP export, and integrated frontend/backend tests.
---

# DiagramCraft Spring Boot Generator

Build the complete generation feature across DiagramCraft's frontend and backend. Keep Django REST Framework as the application's backend; generate Spring Boot as an export artifact rather than replacing Django.

## Inspect before editing

1. Locate both frontend and backend repositories; they may be separate. Read each repository's instructions.
2. Inspect the React pages, editor components, node and edge types, Zustand stores, API modules, routes, styles, and tests.
3. Inspect the Django models, serializers, viewsets, routes, permissions, tests, and generator code.
4. Confirm that the current source of truth is the JSON stored in `Diagrama.nodes` and `Diagrama.edges`. Inspect its actual shape; do not invent field names.
5. Trace one node and one relationship from the React state through the API to Django persistence before changing the contract.
6. Preserve unrelated user changes and existing API behavior.
7. Determine the configured Java, Spring Boot, build-tool, database, package-name, and inheritance defaults. Ask only when a missing choice materially changes the result.

## Preserve the generation boundary

Use this flow:

`React Flow editor -> Diagrama.nodes / Diagrama.edges JSON in Django -> normalized in-memory UML model -> validation -> Jinja2 rendering -> project tree -> ZIP response`

Treat `Diagrama.nodes` and `Diagrama.edges` as the authoritative persisted input. Do not generate from separate legacy `ClaseUML` or `RelacionUML` tables unless the repository explicitly contains an active compatibility or migration requirement. Do not generate Java directly from raw React Flow JSON either: load the authorized `Diagrama`, normalize its JSON into an in-memory UML model, validate that model, and only then render it.

Keep these layers separate:

- Loader: fetch the complete `Diagrama`, enforce access, and read its `nodes` and `edges` JSON.
- Normalizer: convert the raw JSON into a typed, framework-neutral in-memory model; do not persist duplicate class or relationship rows.
- Validator: reject invalid or ambiguous diagrams with actionable messages.
- Mapper: translate UML semantics into Java/JPA semantics.
- Renderer: render deterministic Jinja2 templates.
- Packager: create an isolated temporary project and stream a ZIP.

Never render untrusted identifiers or paths without sanitizing them. Prevent path traversal, filename collisions, invalid Java identifiers, reserved-word conflicts, and duplicate generated types.

## Use the current JSON source of truth

Preserve a single authoritative representation:

- `Diagrama.nodes`: React Flow nodes containing the UML class/interface data needed for generation.
- `Diagrama.edges`: React Flow edges containing relationship semantics and endpoint metadata.

Introduce Python dataclasses, typed dictionaries, Pydantic-style schemas, or equivalent plain in-memory structures for the normalized UML model. This intermediate model is transient and must not become a second persistence system.

Keep React Flow-only presentation data, such as coordinates or selection state, out of the generator domain model. Preserve only generation-relevant semantics: stable node IDs, node kind, names, attributes, methods, modifiers, relationship type, semantic endpoints, multiplicities, roles, navigability, ownership, and generation options.

If legacy `ClaseUML`, `AtributoUML`, or `RelacionUML` models remain in the repository:

- Do not read them during normal generation.
- Do not write duplicate records to keep them synchronized.
- Do not delete or migrate them unless the user requests that scope.
- Document or test any existing compatibility path that must temporarily remain.

## Implement the React frontend

Implement all UI and state needed to author valid generator input; do not reduce the frontend to only a download button.

### Nodes

- Provide explicit `class` and `interface` node kinds in the creation UI, React Flow node data, Zustand state, API payloads, and persisted responses.
- Render an interface with a visible UML stereotype such as `<<interface>>` and prevent entity-only controls from appearing on it.
- Support editing names, attributes, methods, visibility, Java types, abstract status, and persistent/entity status where the existing product scope permits.
- Preserve stable backend identifiers separately from React Flow display identifiers.

### Relationships

- Offer exactly the supported relationship types: association, aggregation, composition, inheritance, realization, and dependency.
- Render the correct UML marker at the semantic end: open diamond for aggregation, filled diamond for composition, hollow triangle with solid line for inheritance, and hollow triangle with dashed line for realization.
- Store semantic endpoints such as `wholeNodeId`, superclass, or interface target explicitly. Never rely solely on the direction in which the user dragged the edge.
- Provide multiplicity controls only for relationships where multiplicity applies.
- Allow editing source and target multiplicities, role/field names, navigability, bidirectionality, and owning side when supported by the backend schema.
- Block or clearly report invalid local connections such as class-to-class realization and interface-as-inheritance-superclass when the product rules disallow them. Repeat all validation in Django.

### Generation controls

- Add a generation dialog or panel consistent with the existing design system.
- Collect only supported options, such as base package, artifact name, Maven or Gradle, database, requested layers, and JPA inheritance strategy.
- Use the project's shared Axios instance and authentication behavior.
- Request the ZIP as binary data, derive a safe filename from `Content-Disposition` when available, trigger one browser download, and revoke the temporary object URL.
- Disable repeated submission while generating and show progress, success, and actionable backend validation errors.
- Map structured backend errors back to the affected node or edge when identifiers are supplied; focus or select that element in the editor.

Do not duplicate the generator's authoritative semantic rules in React. Frontend checks improve interaction; Django validation remains authoritative.

## Keep one frontend/backend contract

Define or extend a single serialized contract for nodes, relationships, and generation options. Keep enum values identical across React and Django. Centralize frontend constants instead of scattering string literals.

For every contract change:

1. Update the `Diagrama.nodes`/`Diagrama.edges` JSON contract and Django validation; create a migration only if the `Diagrama` schema itself changes.
2. Update serializers and validation.
3. Update React API types/constants, state mapping, and forms.
4. Preserve backward compatibility or migrate existing diagrams deliberately.
5. Add a round-trip test proving values survive create, fetch, edit, and generation.

Never make React infer missing semantics that Django needs for generation.

## Support UML node kinds

Support at least:

- `class`: generate a Java class. Add `@Entity` only when the normalized model marks it as persistent.
- `interface`: generate a Java interface. Never add `@Entity` to an interface.

Represent node kind explicitly in persistence or through an equivalent validated field. Do not infer an interface solely because a realization edge targets the node.

## Map the six UML relationship types

### Association

Generate a Java reference or collection and, only for persistent entities, the appropriate JPA association. Determine cardinality and ownership explicitly:

- `1` to `1`: `@OneToOne`
- `1` to `*`: `@OneToMany` / `@ManyToOne`
- `*` to `*`: `@ManyToMany`

Choose one owning side. Use `mappedBy` only on the inverse side. Do not generate two independent join relationships accidentally.

### Aggregation

Treat aggregation as weak whole-part semantics in the domain model. Java and JPA have no aggregation keyword. Generate an association and do not imply lifecycle deletion by default. Avoid `CascadeType.REMOVE` and `orphanRemoval = true` unless the stored diagram or project policy explicitly requests them.

The diamond end identifies the whole/owner end; never derive ownership from drag direction alone.

### Composition

Treat composition as strong whole-part ownership. Generate an association with lifecycle coupling when compatible with cardinality and project policy, commonly `cascade = CascadeType.ALL` and `orphanRemoval = true` on the whole side.

The filled-diamond end is the whole. Validate that a part does not have multiple composite owners unless the model explicitly supports that exception.

### Inheritance

Generate Java `extends`; inheritance is not itself a JPA relationship.

- Require the target to be a class.
- Allow at most one direct superclass per Java class.
- Detect self-inheritance and inheritance cycles.
- When persistent entities participate, generate a configured JPA inheritance strategy on the root: `JOINED`, `SINGLE_TABLE`, or `TABLE_PER_CLASS`.
- Default to `JOINED` only when the project establishes that policy; otherwise require or expose a choice.
- Keep identifiers and discriminator configuration consistent with the selected strategy.

```java
@Entity
@Inheritance(strategy = InheritanceType.JOINED)
public class Persona {
}

@Entity
public class Cliente extends Persona {
}
```

### Realization

Generate Java `implements`; realization is not a JPA relationship.

- Require the source to be a class and the target to be an interface.
- Allow a class to implement multiple interfaces.
- Never translate realization as an entity association or foreign key.
- Generate interface method signatures and either valid implementations or abstract classes when methods are not implemented, according to the normalized model.

```java
public interface Notificable {
    void notificar();
}

public class Pedido implements Notificable {
    @Override
    public void notificar() {
        // TODO: implement
    }
}
```

### Dependency

Represent dependency as compile-time usage, such as a constructor parameter, service field, method parameter, or import only when the diagram stores the usage context. Do not create a database relation, entity field, or foreign key merely because a dependency edge exists. If context is absent, preserve it as metadata or a documented generated TODO rather than inventing behavior.

## Generate the Spring Boot project

Generate a conventional project containing only requested layers, commonly:

```text
project/
├── pom.xml or build.gradle
└── src/main/
    ├── java/<base-package>/
    │   ├── Application.java
    │   ├── entity/
    │   ├── repository/
    │   ├── service/
    │   ├── controller/
    │   └── dto/
    └── resources/application.properties
```

Use Jinja2 templates for deterministic Java and build files. Configure strict undefined-variable behavior. Keep mapping decisions in Python, not buried in templates. Templates should format an already validated intermediate model.

At minimum, support generation of:

- Classes and interfaces.
- Fields, method signatures, visibility, abstract markers, and Java types.
- `extends` and `implements` clauses.
- JPA entities and associations when persistence is enabled.
- Repositories, services, REST controllers, DTOs, and mappers when requested.
- Build dependencies and database configuration without embedding secrets.

Use stable ordering for files, imports, fields, methods, and annotations so the same diagram produces reproducible output.

## Expose the Django API safely

Implement a dedicated action or endpoint such as `POST /api/diagramas/{id}/generar-spring-boot/` according to existing route conventions.

The endpoint must:

1. Authenticate the caller and verify diagram access.
2. Accept only supported configuration values.
3. Return structured `400` errors for validation failures.
4. Generate in an isolated temporary directory.
5. Return a ZIP with the correct content type and filename.
6. Clean temporary files on success and failure.
7. Avoid persisting generated archives unless explicitly required.

## Validate before rendering

Return errors that identify the node or relationship and the corrective action. Validate at least:

- Unique, legal Java type and member names after normalization.
- Supported UML and Java types.
- Valid multiplicities and relationship endpoints.
- Realization targets an interface.
- Inheritance targets a class.
- No inheritance cycles or multiple class inheritance.
- Valid composition ownership.
- Consistent bidirectional association ownership.
- No duplicate fields introduced by relationships.
- Valid package names and safe output paths.

Do not silently reinterpret an invalid relationship.

## Test the implementation

Add focused tests for:

- Each of the six UML relationship types.
- Class and interface generation.
- `extends` plus multiple `implements` in the same class.
- Every supported cardinality and owning-side combination.
- JPA inheritance strategies.
- Invalid realization, inheritance cycles, invalid multiplicity, and duplicate names.
- Authorization and missing diagrams.
- ZIP structure and representative generated file contents.
- Deterministic output across repeated runs.
- Frontend creation and editing of class and interface nodes.
- Frontend rendering and payloads for all six edge types.
- Generation option submission, loading state, validation display, and ZIP download.
- Round-trip compatibility between React payloads and Django serializers.
- Normalization from representative `Diagrama.nodes`/`Diagrama.edges` JSON fixtures into the in-memory UML model.
- Proof that generation does not depend on legacy `ClaseUML` or `RelacionUML` table rows.

When the environment supports it, extract a generated fixture and run the configured Maven or Gradle compile/test command. A syntactically plausible template is not sufficient evidence that the generated backend compiles.

## Completion criteria

Consider the task complete only when:

- Django remains the DiagramCraft runtime backend.
- `Diagrama.nodes` and `Diagrama.edges` remain the only source of truth used by normal generation.
- The normalized UML representation exists only in memory and does not duplicate persistence.
- React can create, edit, save, reload, and visually distinguish classes, interfaces, and all six relationships.
- Frontend and backend share one tested serialization contract.
- The frontend can request and download the generated ZIP and display element-specific validation errors.
- Spring Boot is produced as a downloadable export.
- All six UML relationships retain their correct semantics.
- Interfaces, `extends`, `implements`, and JPA inheritance are validated.
- Generated output is deterministic and free of embedded credentials.
- Relevant backend tests pass and a representative generated project compiles when tooling is available.
