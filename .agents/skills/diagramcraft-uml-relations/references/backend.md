# DiagramCraft backend relationship context

## Current structure

The uploaded backend is Django 6.1.1 with Django REST Framework. Relevant paths:

- `diagramas/models.py`
- `diagramas/serializers.py`
- `diagramas/views.py`
- `diagramas/urls.py`
- `diagramas/migrations/`
- `config/urls.py`

The API mounts `diagramas.urls` below `/api/diagramas/`. The router exposes:

- `/api/diagramas/diagramas/`
- `/api/diagramas/clases/`
- `/api/diagramas/atributos/`
- `/api/diagramas/relaciones/`
- `/api/diagramas/versiones/`

## Existing data model

`Diagrama` belongs to `Proyecto` and stores the active React Flow document in `nodes` and `edges` JSON fields.

`RelacionUML` already exists with:

- `diagrama`
- `clase_origen`
- `clase_destino`
- `tipo`
- `multiplicidad_origen`
- `multiplicidad_destino`

Its legacy choices include association, inheritance, composition, aggregation, and dependency with title-cased stored values. Realization is missing. The serializer currently uses `fields = '__all__'` and performs no cross-diagram validation.

Do not create another `RelacionUML` model. Extend and migrate the existing model when normalized persistence is selected.

## Permissions

`DiagramAccessMixin.allowed_diagrams()` limits objects to projects where the current user is a member. `RelacionUMLViewSet` permits create/update/delete only for `propietario` and `arquitecto`.

Preserve these rules. Harden serializers against foreign IDs: queryset filtering alone does not prevent a submitted source/target from another diagram. Validate:

1. source and target have the submitted diagram ID;
2. the diagram is accessible to the requesting user;
3. a PATCH cannot move one endpoint into another diagram;
4. requested multiplicities are syntactically acceptable;
5. duplicate-policy and self-relation-policy are explicit.

## JSON persistence and collaboration

`DiagramaViewSet.perform_update()` detects changes to `edges` and rejects them for the editor role. The frontend saves the whole `nodes`/`edges` document through this endpoint and also broadcasts it via the diagram WebSocket.

If JSON remains authoritative, backend work should validate the edge schema inside `DiagramaSerializer` rather than creating a second API call per connection. If normalized rows are introduced for generation, synchronize them transactionally from the accepted JSON update or via an explicit command; do not depend on the browser completing multiple requests.

## Migration guidance

When normalizing type values:

1. add the new choice vocabulary and required fields without destroying existing rows;
2. use a data migration to map legacy values;
3. add `realizacion`;
4. update serializers and compatibility output;
5. only then remove legacy acceptance if desired.

Never edit an already-applied migration. Create a new migration.

## Backend verification

Typical Docker commands from the backend root:

```powershell
docker compose up -d
docker compose exec web python manage.py makemigrations --check
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check
docker compose exec web python manage.py test diagramas
```

Use `makemigrations diagramas` instead of `--check` only when intentionally generating a migration.
