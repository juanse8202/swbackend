# Flujo de colaboración

1. El usuario crea un proyecto con `POST /api/proyectos/proyectos/` y el cuerpo
   `{ "nombre": "Sistema transaccional" }`. El backend crea automáticamente
   el `Diagrama principal` de ese proyecto.
2. El creador invita a un usuario que ya está registrado con
   `POST /api/proyectos/proyectos/<proyecto_id>/invitar/`. Se puede enviar
   `{ "email": "persona@ejemplo.com" }`, `{ "username": "persona" }` o
   `{ "usuario_id": 12 }`.
3. Ambos usuarios consultan los diagramas del proyecto mediante
   `GET /api/diagramas/diagramas/` y se conectan al WebSocket del mismo ID:
   `ws/diagramas/<diagrama_id>/`. Así entran a la misma sala y al mismo lienzo.

Solo el creador puede invitar, quitar colaboradores (`DELETE
`/api/proyectos/proyectos/<proyecto_id>/colaboradores/<usuario_id>/`) o cambiar
la configuración del proyecto. Los colaboradores sí pueden editar los
diagramas compartidos.
