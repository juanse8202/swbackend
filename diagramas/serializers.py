import re

from rest_framework import serializers
from .models import Diagrama, ClaseUML, AtributoUML, RelacionUML, VersionDiagrama

class AtributoUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = AtributoUML
        fields = '__all__'

class ClaseUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClaseUML
        fields = '__all__'

class RelacionUMLSerializer(serializers.ModelSerializer):
    class Meta:
        model = RelacionUML
        fields = '__all__'

class VersionDiagramaSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersionDiagrama
        fields = '__all__'
        read_only_fields = ['usuario']

class DiagramaSerializer(serializers.ModelSerializer):
    """Valida el documento React Flow que es la fuente de verdad del lienzo.

    Las tablas ClaseUML y RelacionUML son heredadas y no se usan para validar
    el documento colaborativo actual.  La validación permanece tolerante con
    diagramas antiguos sin ``kind``/``relationType``, pero no permite que una
    relación UML explícita sea semánticamente inválida.
    """

    NODE_KINDS = {'class', 'entity', 'dto', 'enum', 'embeddable', 'interface'}
    CLASS_KINDS = {'class', 'entity'}
    RELATION_TYPES = {
        'asociacion', 'agregacion', 'composicion', 'herencia',
        'realizacion', 'dependencia',
    }
    LEGACY_RELATION_TYPES = {
        'Asociacion': 'asociacion',
        'Agregacion': 'agregacion',
        'Composicion': 'composicion',
        'Herencia': 'herencia',
        'Dependencia': 'dependencia',
        'Realizacion': 'realizacion',
        'oneToOne': 'asociacion',
        'oneToMany': 'asociacion',
        'manyToOne': 'asociacion',
        'manyToMany': 'asociacion',
    }
    MULTIPLICITY_PATTERN = re.compile(r'^(?:N|\*|\d+(?:\.\.(?:\d+|\*))?)$')

    class Meta:
        model = Diagrama
        fields = '__all__'

    @classmethod
    def _relation_type(cls, edge):
        data = edge.get('data') or {}
        if not isinstance(data, dict):
            raise serializers.ValidationError('data debe ser un objeto.')
        raw_type = data.get('relationType')
        if raw_type is None:
            # Diagramas guardados antes de UML explícito representan una
            # asociación JPA; se conservan para no romper el historial.
            return 'asociacion'
        relation_type = cls.LEGACY_RELATION_TYPES.get(raw_type, raw_type)
        if relation_type not in cls.RELATION_TYPES:
            raise serializers.ValidationError(
                f"relationType no soportado: {raw_type!r}."
            )
        return relation_type

    @classmethod
    def _validate_multiplicity(cls, value, field_name):
        if value in (None, ''):
            return
        if not isinstance(value, str) or not cls.MULTIPLICITY_PATTERN.fullmatch(value.strip()):
            raise serializers.ValidationError(
                {field_name: 'Usa valores como 1, 0..1, 0..* o N.'}
            )

    def validate(self, attrs):
        instance = getattr(self, 'instance', None)
        nodes = attrs.get('nodes', instance.nodes if instance else [])
        edges = attrs.get('edges', instance.edges if instance else [])

        if not isinstance(nodes, list):
            raise serializers.ValidationError({'nodes': 'nodes debe ser una lista.'})
        if not isinstance(edges, list):
            raise serializers.ValidationError({'edges': 'edges debe ser una lista.'})

        node_kinds = {}
        node_data = {}
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                raise serializers.ValidationError({'nodes': {index: 'Cada nodo debe ser un objeto.'}})
            node_id = node.get('id')
            if not isinstance(node_id, str) or not node_id.strip():
                raise serializers.ValidationError({
                    'nodes': {index: {'id': 'Se requiere un id de texto no vacío.'}}
                })
            if node_id in node_kinds:
                raise serializers.ValidationError({
                    'nodes': {index: {'id': 'El id del nodo debe ser único.'}}
                })
            data = node.get('data') or {}
            if not isinstance(data, dict):
                raise serializers.ValidationError({'nodes': {index: {'data': 'data debe ser un objeto.'}}})
            kind = data.get('kind')
            if kind is not None and kind not in self.NODE_KINDS:
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'kind': 'Tipo de nodo no soportado.'}}}
                })
            for list_field in ('properties', 'methods'):
                if list_field in data and not isinstance(data[list_field], list):
                    raise serializers.ValidationError({
                        'nodes': {index: {'data': {list_field: 'Debe ser una lista.'}}}
                    })
            properties = data.get('properties', [])
            if not all(isinstance(item, dict) for item in properties):
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'properties': 'Cada atributo debe ser un objeto.'}}}
                })
            id_count = sum('(@Id)' in str(item.get('type', '')) for item in properties)
            if kind == 'entity' and id_count != 1:
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'properties': 'Una entidad requiere exactamente un atributo @Id.'}}}
                })
            if kind in {'interface', 'dto', 'embeddable'} and id_count:
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'properties': f'Un nodo {kind} no puede declarar @Id.'}}}
                })
            if kind == 'interface' and any(item.get('persistent') for item in properties):
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'properties': 'Una interface no puede tener atributos persistentes.'}}}
                })
            if kind == 'enum':
                literals = data.get('literals', data.get('values'))
                if not isinstance(literals, list) or not literals or not all(
                    isinstance(value, str) and value.strip() for value in literals
                ):
                    raise serializers.ValidationError({
                        'nodes': {index: {'data': {'literals': 'Un enum requiere al menos un literal de texto.'}}}
                    })
            if 'abstract' in data and not isinstance(data['abstract'], bool):
                raise serializers.ValidationError({
                    'nodes': {index: {'data': {'abstract': 'Debe ser verdadero o falso.'}}}
                })
            node_kinds[node_id] = kind
            node_data[node_id] = data

        embeddable_names = {
            str(data.get('title') or data.get('nombre') or '').replace('.java', '').strip()
            for node_id, data in node_data.items()
            if node_kinds[node_id] == 'embeddable'
        }
        for index, node in enumerate(nodes):
            data = node_data[node['id']]
            for attribute in data.get('properties', []):
                if attribute.get('embedded'):
                    type_name = str(attribute.get('type') or '').replace('.java', '').strip()
                    if node_kinds[node['id']] != 'entity' or type_name not in embeddable_names:
                        raise serializers.ValidationError({
                            'nodes': {index: {'data': {'properties': 'embedded solo puede referenciar un nodo embeddable desde una entidad.'}}}
                        })

        inheritance_parents = {}
        for index, edge in enumerate(edges):
            if not isinstance(edge, dict):
                raise serializers.ValidationError({'edges': {index: 'Cada relación debe ser un objeto.'}})
            edge_id = edge.get('id')
            if not isinstance(edge_id, str) or not edge_id.strip():
                raise serializers.ValidationError({
                    'edges': {index: {'id': 'Se requiere un id de texto no vacío.'}}
                })
            source = edge.get('source')
            target = edge.get('target')
            if source not in node_kinds or target not in node_kinds:
                raise serializers.ValidationError({
                    'edges': {index: {'source': 'Origen y destino deben existir en nodes.'}}
                })
            try:
                relation_type = self._relation_type(edge)
                data = edge.get('data') or {}
                self._validate_multiplicity(data.get('multiplicidadOrigen'), 'multiplicidadOrigen')
                self._validate_multiplicity(data.get('multiplicidadDestino'), 'multiplicidadDestino')
            except serializers.ValidationError as error:
                raise serializers.ValidationError({'edges': {index: error.detail}}) from error

            source_kind = node_kinds[source]
            target_kind = node_kinds[target]
            annotation = data.get('jpaAnnotation')
            if annotation in {'@OneToOne', '@OneToMany', '@ManyToOne', '@ManyToMany'} and (
                source_kind != 'entity' or target_kind != 'entity'
            ):
                raise serializers.ValidationError({
                    'edges': {index: {'data': {'jpaAnnotation': 'Las relaciones JPA solo pueden unir entidades.'}}}
                })
            if relation_type == 'realizacion':
                if source_kind not in self.CLASS_KINDS:
                    raise serializers.ValidationError({
                        'edges': {index: {'source': 'La realización debe salir de una clase.'}}
                    })
                if target_kind != 'interface':
                    raise serializers.ValidationError({
                        'edges': {index: {'target': 'La realización debe apuntar a un nodo interface.'}}
                    })
            elif relation_type == 'herencia':
                if source == target:
                    raise serializers.ValidationError({
                        'edges': {index: {'target': 'Una clase no puede heredar de sí misma.'}}
                    })
                if source_kind not in self.CLASS_KINDS or target_kind not in self.CLASS_KINDS:
                    raise serializers.ValidationError({
                        'edges': {index: {'target': 'La herencia requiere clase hija y clase padre.'}}
                    })
                inheritance_parents.setdefault(source, []).append(target)

        multiple_parents = next(
            ((child, parents) for child, parents in inheritance_parents.items() if len(set(parents)) > 1),
            None,
        )
        if multiple_parents:
            child, _parents = multiple_parents
            raise serializers.ValidationError({
                'edges': {'herencia': f'La clase {child!r} no puede tener más de una superclase directa.'}
            })

        def has_inheritance_cycle(node_id, visiting, visited):
            if node_id in visiting:
                return True
            if node_id in visited:
                return False
            visiting.add(node_id)
            cycle = any(
                has_inheritance_cycle(parent, visiting, visited)
                for parent in inheritance_parents.get(node_id, [])
            )
            visiting.remove(node_id)
            visited.add(node_id)
            return cycle

        visited = set()
        if any(has_inheritance_cycle(node_id, set(), visited) for node_id in inheritance_parents):
            raise serializers.ValidationError({
                'edges': {'herencia': 'La herencia no puede contener ciclos.'}
            })

        return attrs
