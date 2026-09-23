"""Generación segura de un proyecto Spring Boot desde Diagrama.nodes/edges."""

from __future__ import annotations

import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import DictLoader, Environment, StrictUndefined


JAVA_IDENTIFIER = re.compile(r'^[A-Za-z_$][A-Za-z0-9_$]*$')
PACKAGE_NAME = re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$')
JAVA_RESERVED = {
    'abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch', 'char',
    'class', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum',
    'extends', 'final', 'finally', 'float', 'for', 'goto', 'if', 'implements',
    'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new',
    'package', 'private', 'protected', 'public', 'return', 'short', 'static',
    'strictfp', 'super', 'switch', 'synchronized', 'this', 'throw', 'throws',
    'transient', 'try', 'void', 'volatile', 'while', 'true', 'false', 'null',
}
CLASS_KINDS = {'class', 'entity'}
NODE_KINDS = CLASS_KINDS | {'interface', 'dto', 'enum', 'embeddable'}
LEGACY_RELATIONS = {
    'oneToOne': 'asociacion', 'oneToMany': 'asociacion',
    'manyToOne': 'asociacion', 'manyToMany': 'asociacion',
    'Asociacion': 'asociacion', 'Agregacion': 'agregacion',
    'Composicion': 'composicion', 'Herencia': 'herencia',
    'Realizacion': 'realizacion', 'Dependencia': 'dependencia',
}
VALID_MULTIPLICITIES = {'1', '0..1', '*', '0..*', '1..*', 'N'}


class DiagramGenerationError(Exception):
    def __init__(self, errors):
        super().__init__('El diagrama no puede convertirse a Spring Boot.')
        self.errors = errors


@dataclass
class UmlNode:
    identifier: str
    name: str
    kind: str
    attributes: list[dict] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    parent: str | None = None
    interfaces: list[str] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    id_type: str = 'UUID'
    has_entity_children: bool = False
    abstract: bool = False
    literals: list[str] = field(default_factory=list)

    @property
    def persistent(self):
        return self.kind == 'entity'


def _java_name(value, *, element_id, label):
    name = str(value or '').replace('.java', '').strip()
    if not JAVA_IDENTIFIER.fullmatch(name) or name in JAVA_RESERVED:
        raise DiagramGenerationError([{
            'code': 'invalid_java_identifier', 'element_id': element_id,
            'message': f'{label} {name!r} no es un identificador Java válido.',
        }])
    return name


def _java_class_name(value, *, element_id, label):
    """Normaliza el nombre público Java sin aceptar rutas ni palabras reservadas."""
    name = _java_name(value, element_id=element_id, label=label)
    return name[:1].upper() + name[1:]


def _java_type(value):
    value = str(value or 'String').strip()
    value = re.sub(r'\s*\(@Id\)\s*$', '', value)
    aliases = {'string': 'String', 'integer': 'Integer', 'int': 'Integer', 'uuid': 'UUID', 'long': 'Long', 'decimal': 'BigDecimal', 'boolean': 'Boolean'}
    return aliases.get(value.lower(), value)


def _many(value):
    """True for UML multiplicities that permit more than one instance."""
    return str(value or '1').strip() in {'N', '*', '0..*', '1..*'}


def _relation_annotation(owner_is_source, source_multiplicity, target_multiplicity):
    """Choose the annotation on the owner field from endpoint multiplicities."""
    source_many, target_many = _many(source_multiplicity), _many(target_multiplicity)
    if source_many and target_many:
        return '@ManyToMany', True
    if owner_is_source:
        if target_many:
            return '@OneToMany', True
        if source_many:
            return '@ManyToOne', False
    else:
        if source_many:
            return '@OneToMany', True
        if target_many:
            return '@ManyToOne', False
    return '@OneToOne', False


def _edge_error(code, edge_id, message):
    return {'code': code, 'element_id': edge_id, 'message': message}


def _method_signature(raw):
    raw = str(raw or '').strip().removeprefix('+').removeprefix('-').strip()
    match = re.fullmatch(r'([A-Za-z_$][A-Za-z0-9_$]*)\s*\(([^)]*)\)\s*:\s*([A-Za-z_$][A-Za-z0-9_$<>?, ]*)', raw)
    if not match:
        return f'void {raw or "operacion"}();'
    name, params, return_type = match.groups()
    return f'{_java_type(return_type)} {name}({params});'


def normalize_diagram(nodes, edges):
    errors, result = [], {}
    for raw in nodes:
        node_id = raw.get('id') if isinstance(raw, dict) else None
        data = raw.get('data') if isinstance(raw, dict) else None
        if not isinstance(node_id, str) or not isinstance(data, dict):
            errors.append({'code': 'invalid_node', 'element_id': node_id, 'message': 'Nodo inválido.'})
            continue
        kind = data.get('kind', 'entity')
        if kind not in NODE_KINDS:
            errors.append({'code': 'unsupported_node_kind', 'element_id': node_id, 'message': f'Tipo de nodo no soportado: {kind}.'})
            continue
        try:
            name = _java_class_name(
                data.get('title') or data.get('nombre'), element_id=node_id, label='El nombre'
            )
        except DiagramGenerationError as error:
            errors.extend(error.errors)
            continue
        if any(node.name == name for node in result.values()):
            errors.append({'code': 'duplicate_java_type', 'element_id': node_id, 'message': f'El tipo Java {name} está duplicado.'})
            continue
        attributes = data.get('properties', [])
        if not isinstance(attributes, list):
            errors.append({'code': 'invalid_attributes', 'element_id': node_id, 'message': 'properties debe ser una lista.'})
            continue
        normalized_attributes = []
        for attribute in attributes:
            if not isinstance(attribute, dict):
                errors.append({'code': 'invalid_attribute', 'element_id': node_id, 'message': 'Atributo inválido.'})
                continue
            try:
                attribute_name = _java_name(attribute.get('name'), element_id=node_id, label='El atributo')
            except DiagramGenerationError as error:
                errors.extend(error.errors)
                continue
            attribute_type = _java_type(attribute.get('type'))
            is_id = '(@Id)' in str(attribute.get('type', ''))
            normalized_attributes.append({
                'name': attribute_name,
                'type': attribute_type,
                'id': is_id,
                'accessor': attribute_name[:1].upper() + attribute_name[1:],
                'generated': is_id and attribute_type in {'UUID', 'Long'},
                'embedded': bool(attribute.get('embedded')),
            })
        id_attributes = [attribute for attribute in normalized_attributes if attribute['id']]
        if kind == 'entity' and len(id_attributes) != 1:
            errors.append({
                'code': 'entity_identifier_required',
                'element_id': node_id,
                'message': 'Cada entidad debe tener exactamente un atributo marcado como @Id.',
            })
            continue
        literals = data.get('literals', data.get('values', []))
        if kind == 'enum':
            if not isinstance(literals, list) or not literals:
                errors.append({
                    'code': 'enum_literals_required',
                    'element_id': node_id,
                    'message': 'Un enum requiere al menos un literal.',
                })
                continue
            normalized_literals = []
            for literal in literals:
                try:
                    normalized_literals.append(_java_name(
                        str(literal).upper(), element_id=node_id, label='El literal'
                    ))
                except DiagramGenerationError as error:
                    errors.extend(error.errors)
            literals = normalized_literals
        elif literals not in (None, []) and not isinstance(literals, list):
            errors.append({
                'code': 'invalid_enum_literals', 'element_id': node_id,
                'message': 'literals debe ser una lista.',
            })
            continue
        result[node_id] = UmlNode(
            node_id, name, kind, normalized_attributes, data.get('methods') or [],
            id_type=id_attributes[0]['type'] if id_attributes else 'UUID',
            abstract=bool(data.get('abstract', False)),
            literals=literals,
        )

    by_name = {node.name: node for node in result.values()}
    for node in result.values():
        for attribute in node.attributes:
            referenced = by_name.get(attribute['type'])
            if attribute['embedded'] and (
                node.kind != 'entity' or not referenced or referenced.kind != 'embeddable'
            ):
                errors.append({
                    'code': 'invalid_embedded_attribute',
                    'element_id': node.identifier,
                    'message': 'Un atributo embedded debe referenciar un nodo embeddable desde una entidad.',
                })
        if node.persistent:
            explicit_dto = by_name.get(f'{node.name}Dto')
            if explicit_dto and explicit_dto.kind == 'dto':
                errors.append({
                    'code': 'duplicate_generated_dto',
                    'element_id': explicit_dto.identifier,
                    'message': f'El DTO explícito {explicit_dto.name} colisiona con el DTO CRUD de {node.name}.',
                })

    inheritance = {}
    for raw in edges:
        if not isinstance(raw, dict):
            errors.append({'code': 'invalid_relationship', 'message': 'Relación inválida.'})
            continue
        edge_id, source_id, target_id = raw.get('id'), raw.get('source'), raw.get('target')
        data = raw.get('data') or {}
        relation_type = LEGACY_RELATIONS.get(data.get('relationType'), data.get('relationType') or 'asociacion')
        source, target = result.get(source_id), result.get(target_id)
        if not source or not target:
            errors.append({'code': 'missing_relationship_endpoint', 'element_id': edge_id, 'message': 'La relación referencia un nodo inexistente.'})
            continue
        if relation_type == 'herencia':
            if source.kind not in CLASS_KINDS or target.kind not in CLASS_KINDS or source_id == target_id:
                errors.append({'code': 'invalid_inheritance', 'element_id': edge_id, 'message': 'Herencia requiere clase hija y padre distintos.'})
                continue
            if source.parent and source.parent != target_id:
                errors.append({'code': 'multiple_superclasses', 'element_id': edge_id, 'message': 'Una clase solo puede tener una superclase directa.'})
                continue
            source.parent = target_id
            inheritance[source_id] = target_id
        elif relation_type == 'realizacion':
            if source.kind not in CLASS_KINDS or target.kind != 'interface':
                errors.append({'code': 'invalid_realization', 'element_id': edge_id, 'message': 'Realización requiere clase que implemente una interface.'})
                continue
            source.interfaces.append(target_id)
        elif relation_type in {'asociacion', 'agregacion', 'composicion'}:
            if not source.persistent or not target.persistent:
                if data.get('jpaManaged') is False:
                    # A relationship imported from XMI between ordinary UML
                    # classes is model metadata, not a JPA mapping.  Keep it
                    # in Diagrama.edges but do not invent persistence fields.
                    continue
                errors.append(_edge_error('invalid_jpa_relationship', edge_id, 'Una relación JPA solo puede unir dos entidades persistentes.'))
                continue
            source_multiplicity = data.get('multiplicidadOrigen') or '1'
            target_multiplicity = data.get('multiplicidadDestino') or 'N'
            if source_multiplicity not in VALID_MULTIPLICITIES or target_multiplicity not in VALID_MULTIPLICITIES:
                errors.append(_edge_error('invalid_multiplicity', edge_id, 'Las multiplicidades deben ser 1, 0..1, *, 0..* o 1..*.'))
                continue
            owner_id = data.get('ownerNodeId')
            # Old diagrams were unidirectional and did not store ownership.
            if not owner_id:
                owner_id = (data.get('wholeNodeId') or target_id) if relation_type in {'agregacion', 'composicion'} else source_id
            if owner_id not in {source_id, target_id}:
                errors.append(_edge_error('invalid_relationship_owner', edge_id, 'ownerNodeId debe apuntar a uno de los extremos.'))
                continue
            whole_id = data.get('wholeNodeId')
            if relation_type in {'agregacion', 'composicion'}:
                whole_id = whole_id or (target_id if data.get('relationConvention') == 'target-whole-v1' else None)
                if whole_id not in {source_id, target_id}:
                    errors.append(_edge_error('missing_whole', edge_id, 'La agregación o composición requiere wholeNodeId.'))
                    continue
                if relation_type == 'composicion' and owner_id != whole_id:
                    errors.append(_edge_error('composition_owner_required', edge_id, 'El whole debe ser el propietario de una composición.'))
                    continue
            owner, other = (source, target) if owner_id == source_id else (target, source)
            owner_is_source = owner_id == source_id
            annotation, collection = _relation_annotation(owner_is_source, source_multiplicity, target_multiplicity)
            if relation_type == 'composicion' and annotation not in {'@OneToOne', '@OneToMany'}:
                errors.append(_edge_error('invalid_composition_cardinality', edge_id, 'Una composición debe tener al whole como OneToOne o OneToMany.'))
                continue
            if bool(data.get('bidirectional', False)) and annotation == '@OneToMany':
                errors.append(_edge_error('invalid_bidirectional_owner', edge_id, 'En una relación bidireccional OneToMany, el propietario debe ser el extremo ManyToOne.'))
                continue
            owner_role = data.get('sourceRole' if owner_is_source else 'targetRole') or other.name[:1].lower() + other.name[1:]
            inverse_role = data.get('targetRole' if owner_is_source else 'sourceRole') or owner.name[:1].lower() + owner.name[1:]
            try:
                owner_role = _java_name(owner_role, element_id=edge_id, label='El rol propietario')
                inverse_role = _java_name(inverse_role, element_id=edge_id, label='El rol inverso')
            except DiagramGenerationError as error:
                errors.extend(error.errors)
                continue
            owner.relations.append({
                'target': other.identifier, 'annotation': annotation, 'name': owner_role,
                'collection': collection, 'owner': True,
                'cascade': relation_type == 'composicion',
                'join_column': f'{owner_role}_id',
                'join_table': f'{owner.name.lower()}_{other.name.lower()}',
            })
            if bool(data.get('bidirectional', False)):
                inverse_annotation = {
                    '@OneToMany': '@ManyToOne',
                    '@ManyToOne': '@OneToMany',
                }.get(annotation, annotation)
                other.relations.append({
                    'target': owner.identifier, 'annotation': inverse_annotation, 'name': inverse_role,
                    'collection': inverse_annotation in {'@OneToMany', '@ManyToMany'}, 'owner': False,
                    'mapped_by': owner_role, 'cascade': False,
                })

    for node_id in inheritance:
        visited, cursor = set(), node_id
        while cursor in inheritance:
            if cursor in visited:
                errors.append({'code': 'inheritance_cycle', 'element_id': node_id, 'message': 'La herencia contiene un ciclo.'})
                break
            visited.add(cursor)
            cursor = inheritance[cursor]
    if errors:
        raise DiagramGenerationError(errors)
    for node in result.values():
        used_names = {attribute['name'] for attribute in node.attributes}
        for relation in node.relations:
            if relation['name'] in used_names:
                errors.append(_edge_error('duplicate_relationship_field', node.identifier, f"El campo {relation['name']!r} está duplicado."))
            used_names.add(relation['name'])
    if errors:
        raise DiagramGenerationError(errors)
    for node in result.values():
        if node.parent:
            parent = result[node.parent]
            if node.persistent and parent.persistent:
                parent.has_entity_children = True
    return list(result.values())


TEMPLATES = {
    'pom.xml': '''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>{{ package }}</groupId><artifactId>{{ artifact }}</artifactId><version>0.0.1-SNAPSHOT</version><parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>3.4.0</version></parent><properties><java.version>21</java.version></properties><dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency><dependency><groupId>org.postgresql</groupId><artifactId>postgresql</artifactId><scope>runtime</scope></dependency></dependencies><build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin></plugins></build></project>''',
    'application.java': '''package {{ package }};

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
''',
    'entity.java': '''package {{ package }}.entity;

{% if node.persistent %}import jakarta.persistence.*;
{% endif %}{% if uses_uuid %}import java.util.UUID;
{% endif %}{% if uses_bigdecimal %}import java.math.BigDecimal;
{% endif %}{% if uses_list %}import java.util.List;
{% endif %}{% for import_name in node.imports %}import {{ import_name }};
{% endfor %}
{% if node.persistent %}@Entity
{% if node.has_entity_children %}@Inheritance(strategy = InheritanceType.JOINED)
{% endif %}{% endif %}public {% if node.abstract %}abstract {% endif %}class {{ node.name }}{% if node.parent %} extends {{ node.parent_name }}{% endif %}{% if node.interfaces %} implements {{ node.interface_names|join(', ') }}{% endif %} {
{% for attribute in node.attributes %}{% if attribute.id %}    @Id
{% if attribute.generated %}    @GeneratedValue(strategy = GenerationType.{% if attribute.type == 'UUID' %}UUID{% else %}IDENTITY{% endif %})
{% endif %}{% endif %}{% if attribute.embedded %}    @Embedded
{% endif %}    private {{ attribute.type }} {{ attribute.name }};
{% endfor %}{% for relation in node.relations %}
    {{ relation.annotation }}{% if relation.cascade %}(cascade = CascadeType.ALL, orphanRemoval = true){% elif not relation.owner and relation.annotation in ['@OneToMany', '@OneToOne', '@ManyToMany'] %}(mappedBy = "{{ relation.mapped_by }}"){% endif %}{% if relation.owner and relation.annotation == '@ManyToMany' %}
    @JoinTable(name = "{{ relation.join_table }}"){% elif relation.owner and relation.annotation != '@OneToMany' %}
    @JoinColumn(name = "{{ relation.join_column }}"){% endif %}
    private {% if relation.collection %}List<{{ relation.target_name }}>{% else %}{{ relation.target_name }}{% endif %} {{ relation.name }};
{% endfor %}
    public {{ node.name }}() {
    }
{% for attribute in node.attributes %}
    public {{ attribute.type }} get{{ attribute.accessor }}() {
        return {{ attribute.name }};
    }

    public void set{{ attribute.accessor }}({{ attribute.type }} {{ attribute.name }}) {
        this.{{ attribute.name }} = {{ attribute.name }};
    }
{% endfor %}{% for method in node.class_methods %}
    public {{ method.declaration }} {
        throw new UnsupportedOperationException("Generated method is not implemented yet");
    }
{% endfor %}{% for method in node.implemented_methods %}
    @Override
    public {{ method.declaration }} {
        throw new UnsupportedOperationException("Generated interface method is not implemented yet");
    }
{% endfor %}}
''',
    'interface.java': '''package {{ package }}.model;

public interface {{ node.name }} {
{% for method in node.methods %}    {{ method }}
{% endfor %}}
''',
    'model_class.java': '''package {{ package }}.model;

{% if uses_uuid %}import java.util.UUID;
{% endif %}{% if uses_bigdecimal %}import java.math.BigDecimal;
{% endif %}{% for import_name in node.imports %}import {{ import_name }};
{% endfor %}
public {% if node.abstract %}abstract {% endif %}class {{ node.name }}{% if node.parent %} extends {{ node.parent_name }}{% endif %}{% if node.interfaces %} implements {{ node.interface_names|join(', ') }}{% endif %} {
{% for attribute in node.attributes %}    private {{ attribute.type }} {{ attribute.name }};
{% endfor %}
    public {{ node.name }}() {
    }
{% for attribute in node.attributes %}
    public {{ attribute.type }} get{{ attribute.accessor }}() {
        return {{ attribute.name }};
    }

    public void set{{ attribute.accessor }}({{ attribute.type }} {{ attribute.name }}) {
        this.{{ attribute.name }} = {{ attribute.name }};
    }
{% endfor %}{% for method in node.class_methods %}
    public {{ method.declaration }} {
        throw new UnsupportedOperationException("Generated method is not implemented yet");
    }
{% endfor %}{% for method in node.implemented_methods %}
    @Override
    public {{ method.declaration }} {
        throw new UnsupportedOperationException("Generated interface method is not implemented yet");
    }
{% endfor %}}
''',
    'embeddable.java': '''package {{ package }}.model;

import jakarta.persistence.Embeddable;
{% if uses_uuid %}import java.util.UUID;
{% endif %}{% if uses_bigdecimal %}import java.math.BigDecimal;
{% endif %}{% for import_name in node.imports %}import {{ import_name }};
{% endfor %}
@Embeddable
public class {{ node.name }} {
{% for attribute in node.attributes %}    private {{ attribute.type }} {{ attribute.name }};
{% endfor %}
    public {{ node.name }}() {
    }
{% for attribute in node.attributes %}
    public {{ attribute.type }} get{{ attribute.accessor }}() {
        return {{ attribute.name }};
    }

    public void set{{ attribute.accessor }}({{ attribute.type }} {{ attribute.name }}) {
        this.{{ attribute.name }} = {{ attribute.name }};
    }
{% endfor %}}
''',
    'enum.java': '''package {{ package }}.model;

public enum {{ node.name }} {
{% for literal in node.literals %}    {{ literal }}{% if not loop.last %},{% else %};{% endif %}
{% endfor %}}
''',
    'repository.java': '''package {{ package }}.repository;

{% if node.id_type == 'UUID' %}import java.util.UUID;
{% endif %}import org.springframework.data.jpa.repository.JpaRepository;
import {{ package }}.entity.{{ node.name }};

public interface {{ node.name }}Repository extends JpaRepository<{{ node.name }}, {{ node.id_type }}> {
}
''',
    'dto.java': '''package {{ package }}.dto;

{% if uses_uuid %}import java.util.UUID;
{% endif %}{% if uses_bigdecimal %}import java.math.BigDecimal;
{% endif %}{% for import_name in node.imports %}import {{ import_name }};
{% endfor %}public record {{ node.name }}Dto(
{% for attribute in node.attributes %}    {{ attribute.type }} {{ attribute.name }}{% if not loop.last %},{% endif %}
{% endfor %}) {
}
''',
    'explicit_dto.java': '''package {{ package }}.dto;

{% if uses_uuid %}import java.util.UUID;
{% endif %}{% if uses_bigdecimal %}import java.math.BigDecimal;
{% endif %}{% for import_name in node.imports %}import {{ import_name }};
{% endfor %}public record {{ node.name }}(
{% for attribute in node.attributes %}    {{ attribute.type }} {{ attribute.name }}{% if not loop.last %},{% endif %}
{% endfor %}) {
}
''',
    'mapper.java': '''package {{ package }}.mapper;

import {{ package }}.dto.{{ node.name }}Dto;
import {{ package }}.entity.{{ node.name }};

public final class {{ node.name }}Mapper {
    private {{ node.name }}Mapper() {
    }

    public static {{ node.name }}Dto toDto({{ node.name }} source) {
        if (source == null) {
            return null;
        }
        return new {{ node.name }}Dto({% for attribute in node.attributes %}source.get{{ attribute.accessor }}(){% if not loop.last %}, {% endif %}{% endfor %});
    }

    public static {{ node.name }} toEntity({{ node.name }}Dto source) {
        if (source == null) {
            return null;
        }
        {{ node.name }} target = new {{ node.name }}();
        copy(source, target);
        return target;
    }

    public static void copy({{ node.name }}Dto source, {{ node.name }} target) {
{% for attribute in node.attributes if not attribute.id %}        target.set{{ attribute.accessor }}(source.{{ attribute.name }}());
{% endfor %}    }
}
''',
    'service.java': '''package {{ package }}.service;

import java.util.List;
import java.util.Optional;
{% if node.id_type == 'UUID' %}import java.util.UUID;
{% endif %}import org.springframework.stereotype.Service;
import {{ package }}.dto.{{ node.name }}Dto;
import {{ package }}.entity.{{ node.name }};
import {{ package }}.mapper.{{ node.name }}Mapper;
import {{ package }}.repository.{{ node.name }}Repository;

@Service
public class {{ node.name }}Service {
    private final {{ node.name }}Repository repository;

    public {{ node.name }}Service({{ node.name }}Repository repository) {
        this.repository = repository;
    }

    public List<{{ node.name }}Dto> findAll() {
        return repository.findAll().stream().map({{ node.name }}Mapper::toDto).toList();
    }

    public Optional<{{ node.name }}Dto> findById({{ node.id_type }} id) {
        return repository.findById(id).map({{ node.name }}Mapper::toDto);
    }

    public {{ node.name }}Dto create({{ node.name }}Dto dto) {
        {{ node.name }} entity = {{ node.name }}Mapper.toEntity(dto);
        return {{ node.name }}Mapper.toDto(repository.save(entity));
    }

    public Optional<{{ node.name }}Dto> update({{ node.id_type }} id, {{ node.name }}Dto dto) {
        return repository.findById(id).map(entity -> {
            {{ node.name }}Mapper.copy(dto, entity);
            return {{ node.name }}Mapper.toDto(repository.save(entity));
        });
    }

    public boolean delete({{ node.id_type }} id) {
        if (!repository.existsById(id)) {
            return false;
        }
        repository.deleteById(id);
        return true;
    }
}
''',
    'controller.java': '''package {{ package }}.controller;

import java.util.List;
{% if node.id_type == 'UUID' %}import java.util.UUID;
{% endif %}import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import {{ package }}.dto.{{ node.name }}Dto;
import {{ package }}.service.{{ node.name }}Service;

@RestController
@RequestMapping("/{{ node.route }}")
public class {{ node.name }}Controller {
    private final {{ node.name }}Service service;

    public {{ node.name }}Controller({{ node.name }}Service service) {
        this.service = service;
    }

    @GetMapping
    public List<{{ node.name }}Dto> findAll() {
        return service.findAll();
    }

    @GetMapping("/{id}")
    public ResponseEntity<{{ node.name }}Dto> findById(@PathVariable {{ node.id_type }} id) {
        return service.findById(id).map(ResponseEntity::ok).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @PostMapping
    public ResponseEntity<{{ node.name }}Dto> create(@RequestBody {{ node.name }}Dto dto) {
        return ResponseEntity.status(HttpStatus.CREATED).body(service.create(dto));
    }

    @PutMapping("/{id}")
    public ResponseEntity<{{ node.name }}Dto> update(@PathVariable {{ node.id_type }} id, @RequestBody {{ node.name }}Dto dto) {
        return service.update(id, dto).map(ResponseEntity::ok).orElseGet(() -> ResponseEntity.notFound().build());
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable {{ node.id_type }} id) {
        return service.delete(id) ? ResponseEntity.noContent().build() : ResponseEntity.notFound().build();
    }
}
''',
}


def _render_project(nodes, package, artifact, directory):
    environment = Environment(loader=DictLoader(TEMPLATES), undefined=StrictUndefined, keep_trailing_newline=True)
    package_path = Path(*package.split('.'))
    def write(relative, template, **context):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(environment.get_template(template).render(package=package, artifact=artifact, **context), encoding='utf-8')
    write('pom.xml', 'pom.xml')
    write(Path('src/main/java') / package_path / 'Application.java', 'application.java')
    write('src/main/resources/application.properties', None) if False else (directory / 'src/main/resources').mkdir(parents=True, exist_ok=True)
    (directory / 'src/main/resources/application.properties').write_text('spring.datasource.url=${DB_URL:jdbc:postgresql://localhost:5432/app}\nspring.datasource.username=${DB_USERNAME:postgres}\nspring.datasource.password=${DB_PASSWORD:}\nspring.jpa.hibernate.ddl-auto=update\n', encoding='utf-8')
    (directory / 'README.md').write_text(f'# {artifact}\n\nConfigure DB_URL, DB_USERNAME y DB_PASSWORD antes de ejecutar `./mvnw spring-boot:run`.\n', encoding='utf-8')
    by_id = {node.identifier: node for node in nodes}
    by_name = {node.name: node for node in nodes}

    def node_package(node):
        if node.kind == 'entity':
            return 'entity'
        if node.kind == 'dto':
            return 'dto'
        return 'model'

    for node in nodes:
        node.methods = [_method_signature(method) for method in node.methods]
    for node in nodes:
        node.parent_name = by_id[node.parent].name if node.parent else None
        node.interface_names = [by_id[item].name for item in node.interfaces]
        node.route = node.name[:1].lower() + node.name[1:]
        imports = set()
        current_package = node_package(node)
        for attribute in node.attributes:
            referenced = by_name.get(attribute['type'])
            if referenced and referenced.name != node.name and node_package(referenced) != current_package:
                imports.add(f'{package}.{node_package(referenced)}.{referenced.name}')
        for inherited_id in [node.parent, *node.interfaces]:
            if inherited_id:
                referenced = by_id[inherited_id]
                if node_package(referenced) != current_package:
                    imports.add(f'{package}.{node_package(referenced)}.{referenced.name}')
        node.imports = sorted(imports)
        node.class_methods = [
            {'declaration': method.rstrip(';')} for method in node.methods
        ] if node.kind != 'interface' else []
        node.implemented_methods = [
            {'declaration': method.rstrip(';')}
            for interface_id in node.interfaces
            for method in by_id[interface_id].methods
        ]
        for relation in node.relations:
            relation['target_name'] = by_id[relation['target']].name
        context = {
            'node': node,
            'uses_uuid': any(item['type'] == 'UUID' for item in node.attributes),
            'uses_bigdecimal': any(item['type'] == 'BigDecimal' for item in node.attributes),
            'uses_list': any(
                item.get('collection')
                for item in node.relations
            ),
        }
        if node.kind == 'entity':
            write(Path('src/main/java') / package_path / 'entity' / f'{node.name}.java', 'entity.java', **context)
        elif node.kind == 'class':
            write(Path('src/main/java') / package_path / 'model' / f'{node.name}.java', 'model_class.java', **context)
        elif node.kind == 'interface':
            write(Path('src/main/java') / package_path / 'model' / f'{node.name}.java', 'interface.java', **context)
        elif node.kind == 'embeddable':
            write(Path('src/main/java') / package_path / 'model' / f'{node.name}.java', 'embeddable.java', **context)
        elif node.kind == 'enum':
            write(Path('src/main/java') / package_path / 'model' / f'{node.name}.java', 'enum.java', **context)
        elif node.kind == 'dto':
            write(Path('src/main/java') / package_path / 'dto' / f'{node.name}.java', 'explicit_dto.java', **context)
        if node.persistent:
            layers = [
                ('repository', 'Repository', 'repository.java'),
                ('service', 'Service', 'service.java'),
                ('controller', 'Controller', 'controller.java'),
                ('dto', 'Dto', 'dto.java'),
                ('mapper', 'Mapper', 'mapper.java'),
            ]
            for folder, suffix, template in layers:
                write(
                    Path('src/main/java') / package_path / folder / f'{node.name}{suffix}.java',
                    template,
                    **context,
                )


def generate_spring_boot_zip(nodes, edges, *, artifact='diagramcraft-generated', package='com.diagramcraft.generated'):
    if not PACKAGE_NAME.fullmatch(package):
        raise DiagramGenerationError([{'code': 'invalid_package', 'message': 'El paquete Java debe tener al menos dos segmentos en minúsculas.'}])
    artifact = re.sub(r'[^a-z0-9-]', '-', str(artifact).lower()).strip('-')
    if not artifact:
        raise DiagramGenerationError([{'code': 'invalid_artifact', 'message': 'artifact debe contener letras, números o guiones.'}])
    model = normalize_diagram(nodes, edges)
    with tempfile.TemporaryDirectory(prefix='diagramcraft-spring-') as temporary:
        root = Path(temporary) / artifact
        root.mkdir()
        _render_project(model, package, artifact, root)
        archive = Path(temporary) / f'{artifact}.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for source in sorted(root.rglob('*')):
                if source.is_file():
                    zip_file.write(source, source.relative_to(root.parent))
        return archive.read_bytes(), f'{artifact}.zip'
