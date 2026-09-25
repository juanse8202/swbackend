"""Small, secure UML 2.5 XMI interchange adapter.

The adapter deliberately supports the portable UML core emitted by this
project.  EA extension blocks are detected and reported, never guessed.
"""
from __future__ import annotations

import re
import uuid

from lxml import etree
from .foreign_keys import java_identifier, normalize_legacy_foreign_keys

XMI = 'http://www.omg.org/spec/XMI/20131001'
UML = 'http://www.omg.org/spec/UML/20131001'
UMLDI = 'http://www.omg.org/spec/UML/20131001/UMLDI'
UMLDC = 'http://www.omg.org/spec/UML/20131001/UMLDC'
DC = 'https://diagramcraft.local/xmi/1'
NS = {'xmi': XMI, 'uml': UML, 'umldi': UMLDI, 'dc': UMLDC, 'diagramcraft': DC}
# EA exports include extension and diagram-layout elements for every class.
# These limits comfortably accept normal project packages while retaining
# bounded parsing for untrusted uploads.
MAX_BYTES, MAX_ELEMENTS, MAX_DEPTH = 2 * 1024 * 1024, 20_000, 100
MAX_TEXT_BYTES = 1 * 1024 * 1024
JAVA_MEMBER_PATTERN = re.compile(r'^[A-Za-z_$][A-Za-z0-9_$]*$')


def _normalize_xmi_attribute_names(nodes, warnings):
    """Turn SQL captions exported by EA into safe Java member names.

    This is deliberately an XMI-boundary compatibility step.  It does not
    relax validation for attributes typed manually in DiagramCraft.  EA class
    diagrams frequently use captions such as ``porcentaje de bonificacion``;
    retaining the original caption keeps the round trip understandable while
    the stored UML document remains usable by the Java generator.
    """
    for node in nodes:
        data = node.get('data') or {}
        properties = data.get('properties') or []
        used_names = set()
        for attribute in properties:
            if not isinstance(attribute, dict) or not isinstance(attribute.get('name'), str):
                continue
            original = attribute['name'].strip()
            candidate = original if JAVA_MEMBER_PATTERN.fullmatch(original) else java_identifier(original)
            base, suffix = candidate, 2
            while candidate in used_names:
                candidate = f'{base}_{suffix}'
                suffix += 1
            if candidate != original:
                attribute['name'] = candidate
                attribute.setdefault('sourceLabel', original)
                warnings.append(_warning(
                    'sql_attribute_name_normalized',
                    f'El atributo SQL {original!r} se importó como {candidate!r}.',
                    xmi_id=data.get('xmiId'),
                ))
            used_names.add(candidate)


class XmiError(Exception):
    def __init__(self, code, message, *, xmi_id=None):
        super().__init__(message)
        self.error = {'code': code, 'message': message, **({'xmi_id': xmi_id} if xmi_id else {})}


def _attr(element, name, default=None):
    return element.get(name, element.get(f'{{{XMI}}}{name}', default))


def _local(element):
    return etree.QName(element).localname


def _multiplicity(end):
    lower, upper = end.get('lower'), end.get('upper')
    if lower is None:
        lower_value = next((item for item in end if _local(item) == 'lowerValue'), None)
        lower = lower_value.get('value') if lower_value is not None else None
    if upper is None:
        upper_value = next((item for item in end if _local(item) == 'upperValue'), None)
        upper = upper_value.get('value') if upper_value is not None else None
    # No cardinality in the XMI means "not specified".  Do not silently
    # display it as 1 at either endpoint in DiagramCraft.
    if lower is None and upper is None:
        return ''
    lower = lower or '1'
    upper = upper or '1'
    # ``*`` and ``0..*`` are different UML multiplicities: the latter
    # explicitly permits zero related instances. Preserve EA's lower bound.
    return '0..*' if upper == '*' and lower == '0' else ('1..*' if upper == '*' and lower == '1' else (upper if lower == upper else f'{lower}..{upper}'))


def _end_type(end):
    """Read both portable UML attributes and EA's nested <type xmi:idref>."""
    if end.get('type'):
        return end.get('type')
    type_element = next((item for item in end if _local(item) == 'type'), None)
    if type_element is None:
        return None
    return _attr(type_element, 'idref') or (type_element.get('href') or '').rsplit('#', 1)[-1] or None


def _java_type_from_xmi(value, primitive_types):
    """Map EA's private primitive IDs to the Java names DiagramCraft uses."""
    raw = str(value or 'String').strip()
    primitive = primitive_types.get(raw, raw)
    aliases = {
        'string': 'String', 'char': 'Character', 'character': 'Character',
        'boolean': 'Boolean', 'bool': 'Boolean', 'byte': 'Byte',
        'short': 'Short', 'int': 'Integer', 'integer': 'Integer',
        'long': 'Long', 'float': 'Float', 'double': 'Double',
        'real': 'Double', 'decimal': 'BigDecimal', 'uuid': 'UUID',
    }
    return aliases.get(str(primitive).lower(), primitive)


def _warning(code, message, *, xmi_id=None):
    return {'code': code, 'message': message, **({'xmi_id': xmi_id} if xmi_id else {})}


def _layout(index):
    return {'x': 120 + (index % 4) * 320, 'y': 100 + (index // 4) * 210}


def _ea_id(scope):
    """Create a deterministic EA-shaped GUID without reusing imported IDs."""
    value = str(uuid.uuid5(uuid.NAMESPACE_URL, f'diagramcraft-xmi/{scope}')).upper()
    return f'EAID_{value.replace("-", "_")}'


def _ea_package_id(scope):
    return _ea_id(scope).replace('EAID_', 'EAPK_', 1)


def _ea_diagram_object_id(scope):
    """EA's DiagramObject style references use an eight-hex-digit DUID."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f'diagramcraft-diagram-object/{scope}').hex[:8].upper()


def _ea_visibility(value):
    return {'+': 'public', '-': 'private', '#': 'protected', '~': 'package'}.get(
        str(value or '').lower(), str(value or 'private').lower(),
    )


def _ea_guid(xmi_id):
    """Format an EAID as the brace-delimited GUID used in EA extensions."""
    return '{' + xmi_id.removeprefix('EAID_').replace('_', '-') + '}'


def _write_property_type(attribute, java_type):
    """Write UML's real type reference instead of EA's ignored shorthand."""
    primitive = str(java_type or 'String').strip()
    primitive = primitive.replace(' (@Id)', '')
    primitive_types = {
        'String': 'String', 'Boolean': 'Boolean', 'Integer': 'Integer',
        'Long': 'Integer', 'Double': 'Real', 'Float': 'Real',
    }
    type_element = etree.SubElement(attribute, 'type')
    if primitive in primitive_types:
        type_element.set(f'{{{XMI}}}type', 'uml:PrimitiveType')
        type_element.set('href', f'http://www.omg.org/spec/UML/20131001/PrimitiveTypes.xmi#{primitive_types[primitive]}')
    else:
        type_element.set(f'{{{XMI}}}idref', primitive or 'String')


def _shape_layout(shape):
    """Read EA/UMLDI classifier bounds without trusting malformed geometry."""
    bounds = next((item for item in shape if _local(item) == 'bounds'), None)
    if bounds is None:
        return None
    try:
        x, y = int(float(bounds.get('x'))), int(float(bounds.get('y')))
        width, height = int(float(bounds.get('width'))), int(float(bounds.get('height')))
    except (TypeError, ValueError):
        return None
    if not (-100_000 <= x <= 100_000 and -100_000 <= y <= 100_000
            and 20 <= width <= 10_000 and 20 <= height <= 10_000):
        return None
    return {'position': {'x': x, 'y': y}, 'width': width, 'height': height}


def _bounds_position(element):
    """Return a bounded UMLDI label position, or None for malformed input."""
    bounds = next((item for item in element if _local(item) == 'bounds'), None)
    if bounds is None:
        return None
    try:
        x, y = int(float(bounds.get('x'))), int(float(bounds.get('y')))
    except (TypeError, ValueError):
        return None
    if not (-100_000 <= x <= 100_000 and -100_000 <= y <= 100_000):
        return None
    return {'x': x, 'y': y}


def _edge_layouts(elements):
    """Read EA/UMLDI routes and label positions by semantic connector ID.

    The portable UML model describes relation semantics, while UMLDI is the
    diagram notation.  Keeping the latter in edge.data lets React Flow draw
    EA's route without treating it as a second source of UML truth.
    """
    layouts = {}
    for item in elements:
        if not (_local(item) == 'UMLEdge' or _attr(item, 'type') == 'umldi:UMLEdge'):
            continue
        relation_id = item.get('modelElement')
        if not relation_id:
            continue
        points = []
        labels = []
        for child in item:
            if _local(child) == 'waypoint':
                try:
                    x, y = int(float(child.get('x'))), int(float(child.get('y')))
                except (TypeError, ValueError):
                    continue
                if -100_000 <= x <= 100_000 and -100_000 <= y <= 100_000:
                    points.append({'x': x, 'y': y})
            elif _local(child) == 'ownedElement':
                child_type = _attr(child, 'type')
                if child_type in {'umldi:UMLNameLabel', 'umldi:UMLMultiplicityLabel'}:
                    labels.append({
                        'type': child_type,
                        'text': child.get('text', ''),
                        'modelElement': child.get('modelElement'),
                        'position': _bounds_position(child),
                    })
        layouts[relation_id] = {
            'source': item.get('source'), 'target': item.get('target'),
            'points': points, 'labels': labels,
        }
    return layouts


def parse_xmi(payload: bytes):
    if not payload or len(payload) > MAX_BYTES:
        raise XmiError('file_too_large', 'El archivo XMI está vacío o excede 2 MB.')
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise XmiError('unsafe_xml', 'DTD y entidades XML no están permitidos.')
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True)
        root = etree.fromstring(payload, parser=parser)
    except etree.XMLSyntaxError as error:
        raise XmiError('malformed_xml', 'El XMI no es XML válido.') from error
    elements = list(root.iter())
    if (len(elements) > MAX_ELEMENTS
            or max((len(list(item.iterancestors())) for item in elements), default=0) > MAX_DEPTH
            or sum(len(item.text or '') for item in elements) > MAX_TEXT_BYTES):
        raise XmiError('xml_limits_exceeded', 'El XMI excede los límites estructurales permitidos.')
    documentation = next((item for item in root if _local(item) == 'Documentation'), None)
    exporter = ((documentation.get('exporter') if documentation is not None else None)
                or root.get('exporter') or root.get(f'{{{DC}}}exporter') or 'desconocido')
    is_ea = 'enterprise architect' in exporter.lower() or any(
        'enterprise architect' in (value or '').lower() for value in root.attrib.values()
    )
    version = root.get(f'{{{XMI}}}version') or root.get('version')
    # EA's “UML 2.5 (XMI 2.5.1)” exporter commonly serializes the XMI
    # transport version as 2.1.  Accept those documented XMI 2.x transports
    # only when an official UML namespace is present.
    uml_namespaces = [uri for uri in root.nsmap.values() if uri and 'omg.org/spec/UML' in uri]
    if (version not in {'2.1', '2.4', '2.5', '2.5.1'} and not (is_ea and version is None)) or not uml_namespaces:
        raise XmiError('unsupported_xmi_version', 'Se requiere XMI 2.x con un namespace UML oficial compatible.')
    warnings = []
    if is_ea and version is None:
        version = 'EA UML 2.5.1 (xmi:version omitido)'
    dialect = 'Enterprise Architect UML 2.5.1 core' if is_ea else 'DiagramCraft UML 2.5.1 core'
    if is_ea:
        warnings.append(_warning('ea_core_only', 'Se detectó Enterprise Architect; se usa el subconjunto UML core documentado.'))

    nodes, edges, id_map = [], [], {}
    # EA declares its Java primitives under private IDs such as EAJava_int.
    # Resolve those IDs from the XMI document instead of leaking them into
    # DiagramCraft properties or generated Java source.
    primitive_types = {
        _attr(item, 'id'): item.get('name')
        for item in elements
        if _attr(item, 'type') == 'uml:PrimitiveType'
        and _attr(item, 'id') and item.get('name')
    }
    # EA may retain an obsolete duplicate classifier in the package while the
    # actual class diagram points to the current classifier through
    # UMLClassifierShape@modelElement. Keep that information to select the
    # model element the user actually sees.
    visible_classifier_ids = {
        item.get('modelElement') for item in elements
        if (
            _local(item) == 'UMLClassifierShape'
            or _attr(item, 'type') == 'umldi:UMLClassifierShape'
        ) and item.get('modelElement')
    }
    # EA stores the actual canvas layout in UMLDI. Keep it when present so
    # import -> edit -> export does not discard the user's diagram layout.
    shape_layouts = {
        item.get('modelElement'): layout
        for item in elements
        if (_local(item) == 'UMLClassifierShape'
            or _attr(item, 'type') == 'umldi:UMLClassifierShape')
        and item.get('modelElement')
        for layout in [_shape_layout(item)]
        if layout is not None
    }
    edge_layouts = _edge_layouts(elements)
    relation_records = []
    seen_ids = set()
    for element in elements:
        kind = _attr(element, 'type')
        xmi_id = _attr(element, 'id')
        # EA repeats connector IDs in extension/reference blocks.  Duplicate
        # IDs are unsafe only for UML elements that define model semantics.
        if xmi_id and kind in {
            'uml:Class', 'uml:Interface', 'uml:Association', 'uml:Dependency',
            'uml:Package', 'uml:Generalization', 'uml:InterfaceRealization',
        }:
            if xmi_id in seen_ids:
                raise XmiError('duplicate_xmi_id', 'ID XMI duplicado.', xmi_id=xmi_id)
            seen_ids.add(xmi_id)
        if kind in {'uml:Class', 'uml:Interface'} and xmi_id:
            node_kind = 'interface' if kind == 'uml:Interface' else ('entity' if element.get(f'{{{DC}}}kind') == 'entity' else 'class')
            title = element.get('name') or f'Type{xmi_id}'
            attributes = []
            methods = []
            for child in element:
                if _local(child) == 'ownedAttribute':
                    attr_type = _java_type_from_xmi(_end_type(child), primitive_types)
                    if child.get(f'{{{DC}}}id') == 'true':
                        attr_type = f'{attr_type} (@Id)'
                    attribute = {
                        'visibility': child.get('visibility', '+'),
                        'name': child.get('name', 'atributo'), 'type': attr_type,
                    }
                    for xmi_key, document_key in (
                        ('sourceLabel', 'sourceLabel'), ('foreignTable', 'foreignTable'),
                        ('foreignKeyMode', 'foreignKeyMode'),
                    ):
                        value = child.get(f'{{{DC}}}{xmi_key}')
                        if value:
                            attribute[document_key] = value
                    if child.get(f'{{{DC}}}sqlForeignKey') == 'true':
                        attribute['sqlForeignKey'] = True
                    attributes.append(attribute)
                elif _local(child) == 'ownedOperation':
                    methods.append(f"{child.get('name', 'operacion')}(): {child.get('type', 'void')}")
                elif _local(child) == 'generalization':
                    relation_records.append(('herencia', xmi_id, child.get('general'), {}))
                elif _local(child) == 'interfaceRealization':
                    relation_records.append(('realizacion', xmi_id, child.get('contract'), {}))
            node_id = f'xmi-{xmi_id}'
            id_map[xmi_id] = node_id
            layout = shape_layouts.get(xmi_id)
            node = {
                'id': node_id, 'type': 'umlClass',
                'position': (layout or {}).get('position', _layout(len(nodes))),
                'data': {
                    'kind': node_kind, 'title': f'{title}.java',
                    'abstract': element.get('isAbstract') == 'true',
                    'properties': attributes, 'methods': methods, 'xmiId': xmi_id,
                },
            }
            if layout is not None:
                node['width'], node['height'] = layout['width'], layout['height']
            nodes.append(node)
        elif kind in {'uml:Association', 'uml:Dependency'}:
            relation_records.append((kind, xmi_id, element, {}))

    for relation_type, first, second, _ in relation_records:
        if relation_type in {'herencia', 'realizacion'}:
            if first not in id_map or second not in id_map:
                warnings.append(_warning('unresolved_reference', f'Se omitió {relation_type} con referencia no resuelta.', xmi_id=first))
                continue
            data = {'relationType': relation_type, 'multiplicidadOrigen': '', 'multiplicidadDestino': '', 'xmiImported': True, 'xmiRelationId': first}
            layout = edge_layouts.get(first)
            if layout and len(layout['points']) >= 2:
                data['xmiWaypoints'] = layout['points']
            edges.append({'id': f'xmi-rel-{len(edges)}', 'source': id_map[first], 'target': id_map[second], 'type': 'relationEdge', 'data': data})
            continue
        element = second
        if relation_type == 'uml:Dependency':
            source, target = element.get('client'), element.get('supplier')
            type_name, data = 'dependencia', {
                'multiplicidadOrigen': '', 'multiplicidadDestino': '',
                'xmiImported': True,
            }
        else:
            ends = [item for item in element if _local(item) in {'ownedEnd', 'ownedAttribute'}]
            if len(ends) != 2:
                warnings.append(_warning('unsupported_association', 'Se omitió una asociación sin exactamente dos extremos.', xmi_id=first))
                continue
            source, target = _end_type(ends[0]), _end_type(ends[1])
            aggregation = next((item.get('aggregation') for item in ends if item.get('aggregation') in {'shared', 'composite'}), None)
            type_name = 'composicion' if aggregation == 'composite' else ('agregacion' if aggregation == 'shared' else 'asociacion')
            whole_index = next((i for i, item in enumerate(ends) if item.get('aggregation') in {'shared', 'composite'}), None)
            data = {
                # EA stores the relationship caption (for example,
                # "created_by") on uml:Association@name.
                'umlLabel': element.get('name', ''),
                'multiplicidadOrigen': _multiplicity(ends[0]),
                'multiplicidadDestino': _multiplicity(ends[1]),
                'sourceRole': ends[0].get('name', ''),
                'targetRole': ends[1].get('name', ''),
                'bidirectional': True,
                'xmiImported': True,
                # EA imports describe UML semantics.  Unless both endpoints
                # are explicitly modeled as DiagramCraft entities, this must
                # not be converted into a JPA association on generation.
                'jpaManaged': False,
            }
            # Preserve explicit UML navigability when EA provides it.  Omit
            # the fields when absent so older fixtures keep their legacy
            # shape while a round trip does not lose endpoint semantics.
            for end_index, field in ((0, 'sourceNavigable'), (1, 'targetNavigable')):
                raw_navigable = ends[end_index].get('isNavigable')
                if raw_navigable in {'true', 'false'}:
                    data[field] = raw_navigable == 'true'
            if 'sourceNavigable' in data or 'targetNavigable' in data:
                data['bidirectional'] = data.get('sourceNavigable', True) and data.get('targetNavigable', True)
            raw_jpa_managed = element.get(f'{{{DC}}}jpaManaged')
            if raw_jpa_managed in {'true', 'false'}:
                data['jpaManaged'] = raw_jpa_managed == 'true'
            if whole_index is not None:
                data['wholeNodeId'] = id_map.get(_end_type(ends[whole_index]))
        if source not in id_map or target not in id_map:
            warnings.append(_warning('unresolved_reference', f'Se omitió {type_name} con referencia no resuelta.', xmi_id=first))
            continue
        data['xmiRelationId'] = first
        layout = edge_layouts.get(first)
        if layout:
            # UMLDI labels are positioned independently of the connector.
            # The core model remains authoritative for their text, but EA's
            # positions make dense diagrams readable after import.
            end_ids = [
                _attr(end, 'id') for end in ends
            ] if relation_type == 'uml:Association' else []
            label_positions = {}
            for label in layout['labels']:
                position = label.get('position')
                if position is None:
                    continue
                if label['type'] == 'umldi:UMLNameLabel':
                    label_positions['name'] = position
                    if not data.get('umlLabel') and label.get('text'):
                        data['umlLabel'] = label['text']
                elif label.get('modelElement') in end_ids:
                    label_positions['source' if label['modelElement'] == end_ids[0] else 'target'] = position
            if label_positions:
                data['xmiLabelPositions'] = label_positions
            points = layout['points']
            if len(points) >= 2:
                # UMLDI names source/target explicitly.  Normalize the route
                # to the semantic source -> target order used by React Flow.
                if layout.get('source') == target and layout.get('target') == source:
                    points = list(reversed(points))
                data['xmiWaypoints'] = points
        data['ownerNodeId'] = data.get('wholeNodeId') or id_map[source]
        edges.append({'id': f'xmi-rel-{len(edges)}', 'source': id_map[source], 'target': id_map[target], 'type': 'relationEdge', 'data': {'relationType': type_name, **data}})
    # Resolve EA's duplicate classifier records only when exactly one of them
    # is shown in the exported diagram. All links to an obsolete duplicate
    # are redirected to the visible classifier, then repeated edges collapse.
    nodes_by_title = {}
    for node in nodes:
        nodes_by_title.setdefault(node['data']['title'], []).append(node)
    aliases = {}
    retained_ids = {node['id'] for node in nodes}
    for title, same_name_nodes in nodes_by_title.items():
        visible = [node for node in same_name_nodes if node['data'].get('xmiId') in visible_classifier_ids]
        if len(same_name_nodes) > 1 and len(visible) == 1:
            retained = visible[0]
            for duplicate in same_name_nodes:
                if duplicate['id'] != retained['id']:
                    aliases[duplicate['id']] = retained['id']
                    retained_ids.discard(duplicate['id'])
            warnings.append(_warning(
                'duplicate_classifier_resolved',
                f'Se conservó el clasificador visible {title}; se fusionaron duplicados internos de EA.',
                xmi_id=retained['data'].get('xmiId'),
            ))
    if aliases:
        nodes = [node for node in nodes if node['id'] in retained_ids]
        unique_edges, edge_keys = [], set()
        for edge in edges:
            edge['source'] = aliases.get(edge['source'], edge['source'])
            edge['target'] = aliases.get(edge['target'], edge['target'])
            edge_data = edge.get('data') or {}
            for reference_field in ('wholeNodeId', 'ownerNodeId'):
                if edge_data.get(reference_field) in aliases:
                    edge_data[reference_field] = aliases[edge_data[reference_field]]
            key = (
                edge['data'].get('xmiRelationId'), edge['source'], edge['target'], edge['data'].get('relationType'),
                edge['data'].get('multiplicidadOrigen'), edge['data'].get('multiplicidadDestino'),
            )
            if key not in edge_keys:
                edge_keys.add(key)
                unique_edges.append(edge)
        edges = unique_edges
    # First expand EA's visual FK captions (``campo(FK; tabla)``), then make
    # remaining SQL-style column captions safe Java member identifiers.
    # EA exports the actual UML associations separately.  Its SQL-looking FK
    # captions are member labels, not a request to manufacture more edges.
    nodes, edges, foreign_key_warnings = normalize_legacy_foreign_keys(
        nodes, edges, create_missing_relations=False,
    )
    warnings.extend(foreign_key_warnings)
    _normalize_xmi_attribute_names(nodes, warnings)
    uml_non_persistent = sum(
        1 for edge in edges
        if edge.get('data', {}).get('relationType') in {'asociacion', 'agregacion', 'composicion'}
        and edge.get('data', {}).get('jpaManaged') is False
    )
    return {'nodes': nodes, 'edges': edges, 'report': {'dialect': dialect, 'xmi_version': version, 'exporter': exporter, 'imported': {'nodes': len(nodes), 'edges': len(edges)}, 'umlNonPersistentRelations': uml_non_persistent, 'warnings': warnings, 'omitted': warnings, 'errors': []}}


def _bounds_for_node(node, index):
    """Return deterministic EA/UMLDI bounds from React Flow coordinates."""
    position = node.get('position') or _layout(index)
    x = int(float(position.get('x', _layout(index)['x'])))
    y = int(float(position.get('y', _layout(index)['y'])))
    data = node.get('data') or {}
    width = int(float(node.get('width') or 220))
    height = int(float(node.get('height') or max(90, 58 + 20 * len(data.get('properties') or []))))
    return x, y, width, height


def _route_for_edge(data, start, finish):
    """Keep a valid imported UMLDI route; otherwise use a straight route."""
    points = (data or {}).get('xmiWaypoints')
    if not isinstance(points, list) or len(points) < 2:
        return [start, finish]
    route = []
    for point in points:
        try:
            x, y = int(float(point.get('x'))), int(float(point.get('y')))
        except (AttributeError, TypeError, ValueError):
            return [start, finish]
        if not (-100_000 <= x <= 100_000 and -100_000 <= y <= 100_000):
            return [start, finish]
        route.append((x, y))
    return route


def _multiplicity_values(value):
    """Return canonical UML lower/upper/text only for an explicit value."""
    text = str(value or '').strip()
    if not text:
        return None
    normalized = text.replace(' ', '').upper()
    mapping = {
        '*': ('0', '*', '0..*'),
        'N': ('0', '*', '0..*'),
        '0..*': ('0', '*', '0..*'),
        '0..N': ('0', '*', '0..*'),
        '1..*': ('1', '*', '1..*'),
        '1..N': ('1', '*', '1..*'),
        '0..1': ('0', '1', '0..1'),
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.isdigit():
        return normalized, normalized, normalized
    if '..' in normalized:
        lower, upper = normalized.split('..', 1)
        if lower.isdigit() and (upper.isdigit() or upper == '*'):
            return lower, upper, f'{lower}..{upper}'
    # Keep an explicit custom cardinality rather than silently replacing it.
    return text, text, text


def _write_multiplicity(end, value, relation_index, end_index, relation_id):
    """Write EA's UML 2.5 multiplicity values and return data for its UMLDI label."""
    values = _multiplicity_values(value)
    if values is None:
        return None
    lower, upper, text = values
    for suffix, xmi_type, literal in (
        ('lower', 'uml:LiteralInteger', lower),
        ('upper', 'uml:LiteralUnlimitedNatural', upper),
    ):
        child = etree.SubElement(end, f'{suffix}Value')
        child.set(f'{{{XMI}}}type', xmi_type)
        child.set(f'{{{XMI}}}id', f'dc-{suffix}-{relation_index}-{end_index}')
        child.set('value', literal)
    return {'text': text, 'end_id': end.get(f'{{{XMI}}}id'), 'relation_id': relation_id}


def _label_bounds(start, end, text, near_start):
    """Place a deterministic UMLDI label near one endpoint of a straight edge."""
    sx, sy = start
    tx, ty = end
    ratio = 0.08 if near_start else 0.92
    x = round(sx + (tx - sx) * ratio)
    y = round(sy + (ty - sy) * ratio)
    # A small perpendicular offset keeps the label off the connector itself.
    dx, dy = tx - sx, ty - sy
    offset_x = -7 if dy >= 0 else 7
    offset_y = 7 if dx >= 0 else -7
    return x + offset_x, y + offset_y, max(6, len(text) * 7), 14


def export_xmi(nodes, edges, *, diagram_name='DiagramCraft'):
    """Export UML semantics plus an EA-readable UMLDI class diagram."""
    nodes, edges, _warnings = normalize_legacy_foreign_keys(nodes, edges)
    diagram_name = str(diagram_name or 'DiagramCraft').strip() or 'DiagramCraft'
    root = etree.Element(f'{{{XMI}}}XMI', nsmap=NS)
    # EA 6.5's own XMI 2.5.1 exports (including Finanzas.xmi) omit the
    # transport version. Keeping that dialect avoids EA taking its generic
    # XMI 2.5 path, which imports classifiers but ignores its diagram block.
    root.set(f'{{{DC}}}exporter', 'DiagramCraft UML 2.5.1 core')
    documentation = etree.SubElement(root, f'{{{XMI}}}Documentation')
    documentation.set('exporter', 'Enterprise Architect')
    documentation.set('exporterVersion', '6.5')
    model = etree.SubElement(root, f'{{{UML}}}Model')
    model.set(f'{{{XMI}}}type', 'uml:Model')
    model_id = _ea_id(f'model:{diagram_name}')
    model.set(f'{{{XMI}}}id', model_id)
    model.set('name', 'EA_Model')
    package = etree.SubElement(model, 'packagedElement')
    package.set(f'{{{XMI}}}type', 'uml:Package')
    package_id = _ea_package_id(f'package:{diagram_name}')
    # EA's extension links ``package2`` to the same GUID used by the UML
    # package, differing only in its EAID/EAPK prefix (see Finanzas.xmi).
    package_model_id = package_id.replace('EAPK_', 'EAID_', 1)
    package.set(f'{{{XMI}}}id', package_id)
    package.set('name', 'DiagramCraft')

    ids, model_items, diagram_nodes, attribute_records = {}, {}, [], {}
    for index, node in enumerate(nodes):
        data, node_id = node.get('data') or {}, node.get('id')
        kind = data.get('kind', 'entity')
        if kind not in {'entity', 'class', 'interface'}:
            continue
        # xmiId is provenance from an imported EA model. Reusing it when the
        # user exports back into the same EA project collides with the source
        # classifier (for example Finanzas.auth_user).  An export owns a fresh
        # namespace, while DiagramCraft's node id remains the stable mapping.
        xmi_id = _ea_id(f'node:{node_id}')
        ids[node_id] = xmi_id
        item = etree.SubElement(package, 'packagedElement')
        item.set(f'{{{XMI}}}type', 'uml:Interface' if kind == 'interface' else 'uml:Class')
        item.set(f'{{{XMI}}}id', xmi_id)
        item.set('name', str(data.get('title', 'Tipo')).removesuffix('.java'))
        item.set(f'{{{DC}}}kind', kind)
        item.set(f'{{{DC}}}nodeId', str(node_id))
        if data.get('abstract'):
            item.set('isAbstract', 'true')
        attribute_records[xmi_id] = []
        for attribute_index, attribute in enumerate(data.get('properties', [])):
            if not isinstance(attribute, dict):
                continue
            attribute_id = _ea_id(f'attribute:{node_id}:{attribute_index}')
            attribute_type = str(attribute.get('type', 'String')).replace(' (@Id)', '')
            child = etree.SubElement(item, 'ownedAttribute')
            child.set(f'{{{XMI}}}type', 'uml:Property')
            child.set(f'{{{XMI}}}id', attribute_id)
            child.set('name', str(attribute.get('name', 'atributo')))
            child.set('visibility', _ea_visibility(attribute.get('visibility', '+')))
            _write_property_type(child, attribute_type)
            attribute_records[xmi_id].append({
                'id': attribute_id, 'name': str(attribute.get('name', 'atributo')),
                'type': attribute_type, 'visibility': _ea_visibility(attribute.get('visibility', '+')),
            })
            if '(@Id)' in str(attribute.get('type', '')):
                child.set(f'{{{DC}}}id', 'true')
            for document_key, xmi_key in (
                ('sourceLabel', 'sourceLabel'), ('foreignTable', 'foreignTable'),
                ('foreignKeyMode', 'foreignKeyMode'),
            ):
                if attribute.get(document_key):
                    child.set(f'{{{DC}}}{xmi_key}', str(attribute[document_key]))
            if attribute.get('sqlForeignKey'):
                child.set(f'{{{DC}}}sqlForeignKey', 'true')
        for method in data.get('methods', []):
            child = etree.SubElement(item, 'ownedOperation')
            child.set(f'{{{XMI}}}type', 'uml:Operation')
            child.set(f'{{{XMI}}}id', _ea_id(f'operation:{node_id}:{method}'))
            child.set('name', str(method).split('(')[0].strip() or 'operacion')
            _write_property_type(child, str(method).split(':')[-1].strip() if ':' in str(method) else 'void')
        model_items[xmi_id] = item
        diagram_nodes.append((node, xmi_id))

    diagram_edges = []
    for index, edge in enumerate(edges):
        data = edge.get('data') or {}
        source, target = ids.get(edge.get('source')), ids.get(edge.get('target'))
        if not source or not target:
            continue
        rel = data.get('relationType', 'asociacion')
        relation_id = _ea_id(f'relation:{edge.get("id", index)}')
        multiplicity_labels = []
        if rel == 'herencia':
            item = model_items.get(source)
            if item is None:
                continue
            semantic = etree.SubElement(item, 'generalization')
            semantic.set(f'{{{XMI}}}id', relation_id)
            semantic.set('general', target)
        elif rel == 'realizacion':
            item = model_items.get(source)
            if item is None:
                continue
            semantic = etree.SubElement(item, 'interfaceRealization')
            semantic.set(f'{{{XMI}}}id', relation_id)
            semantic.set('contract', target)
        else:
            semantic = etree.SubElement(package, 'packagedElement')
            semantic.set(f'{{{XMI}}}id', relation_id)
            semantic.set(f'{{{DC}}}edgeId', str(edge.get('id', index)))
            if rel == 'dependencia':
                semantic.set(f'{{{XMI}}}type', 'uml:Dependency')
                semantic.set('client', source)
                semantic.set('supplier', target)
            else:
                semantic.set(f'{{{XMI}}}type', 'uml:Association')
                if isinstance(data.get('jpaManaged'), bool):
                    semantic.set(f'{{{DC}}}jpaManaged', str(data['jpaManaged']).lower())
                if data.get('umlLabel'):
                    semantic.set('name', str(data['umlLabel']))
                whole = ids.get(data.get('wholeNodeId'))
                end_ids = []
                for end_index in range(2):
                    end_id = _ea_id(f'end:{edge.get("id", index)}:{end_index}')
                    end_ids.append(end_id)
                    member_end = etree.SubElement(semantic, 'memberEnd')
                    member_end.set(f'{{{XMI}}}idref', end_id)
                for end_index, endpoint in enumerate((source, target)):
                    end = etree.SubElement(semantic, 'ownedEnd')
                    end.set(f'{{{XMI}}}type', 'uml:Property')
                    end.set(f'{{{XMI}}}id', end_ids[end_index])
                    end.set('association', relation_id)
                    type_ref = etree.SubElement(end, 'type')
                    type_ref.set(f'{{{XMI}}}idref', endpoint)
                    role = data.get('sourceRole' if end_index == 0 else 'targetRole')
                    if role:
                        end.set('name', str(role))
                    navigable = data.get('sourceNavigable' if end_index == 0 else 'targetNavigable')
                    if isinstance(navigable, bool):
                        end.set('isNavigable', str(navigable).lower())
                    label = _write_multiplicity(
                        end,
                        data.get('multiplicidadOrigen' if end_index == 0 else 'multiplicidadDestino'),
                        index,
                        end_index,
                        relation_id,
                    )
                    if label is not None:
                        label['near_start'] = end_index == 0
                        multiplicity_labels.append(label)
                    if rel in {'agregacion', 'composicion'} and whole == endpoint:
                        end.set('aggregation', 'composite' if rel == 'composicion' else 'shared')
        diagram_edges.append((relation_id, source, target, data, multiplicity_labels))

    ea_diagram_id = _ea_id(f'diagram:{diagram_name}')
    diagram = etree.SubElement(root, f'{{{UMLDI}}}Diagram')
    diagram.set(f'{{{XMI}}}type', 'umldi:UMLClassDiagram')
    diagram.set(f'{{{XMI}}}id', ea_diagram_id)
    diagram.set('name', diagram_name)
    diagram.set('isFrame', 'false')
    diagram.set('modelElement', package_id)
    node_bounds = {
        xmi_id: _bounds_for_node(node, index)
        for index, (node, xmi_id) in enumerate(diagram_nodes)
    }
    diagram_object_ids = {
        xmi_id: _ea_diagram_object_id(xmi_id)
        for _, xmi_id in diagram_nodes
    }
    for index, (node, xmi_id) in enumerate(diagram_nodes):
        shape = etree.SubElement(diagram, 'ownedElement')
        shape.set(f'{{{XMI}}}type', 'umldi:UMLClassifierShape')
        shape.set(f'{{{XMI}}}id', _ea_id(f'shape:{xmi_id}'))
        shape.set('modelElement', xmi_id)
        name = etree.SubElement(shape, 'ownedElement')
        name.set(f'{{{XMI}}}type', 'umldi:UMLNameLabel')
        name.set(f'{{{XMI}}}id', _ea_id(f'name:{xmi_id}'))
        name.set('text', str((node.get('data') or {}).get('title', 'Tipo')).removesuffix('.java'))
        x, y, width, height = node_bounds[xmi_id]
        bounds = etree.SubElement(shape, 'bounds')
        bounds.set(f'{{{XMI}}}type', 'dc:bounds')
        bounds.set(f'{{{XMI}}}id', _ea_id(f'bounds:{xmi_id}'))
        bounds.set('x', str(x)); bounds.set('y', str(y))
        bounds.set('width', str(width)); bounds.set('height', str(height))
    for index, (relation_id, source, target, data, multiplicity_labels) in enumerate(diagram_edges):
        edge = etree.SubElement(diagram, 'ownedElement')
        edge.set(f'{{{XMI}}}type', 'umldi:UMLEdge')
        edge.set(f'{{{XMI}}}id', _ea_id(f'diagram-edge:{relation_id}'))
        edge.set('source', source)
        edge.set('target', target)
        edge.set('modelElement', relation_id)
        # A deterministic straight route is enough for EA to bind the edge;
        # EA can subsequently reroute it in the diagram editor.
        source_bounds, target_bounds = node_bounds.get(source), node_bounds.get(target)
        if source_bounds is not None and target_bounds is not None:
            sx, sy, sw, sh = source_bounds
            tx, ty, tw, th = target_bounds
            start = (sx + sw // 2, sy + sh // 2)
            finish = (tx + tw // 2, ty + th // 2)
            route = _route_for_edge(data, start, finish)
            label_positions = data.get('xmiLabelPositions') or {}
            for label_index, label in enumerate(multiplicity_labels):
                label_item = etree.SubElement(edge, 'ownedElement')
                label_item.set(f'{{{XMI}}}type', 'umldi:UMLMultiplicityLabel')
                label_item.set(f'{{{XMI}}}id', _ea_id(f'multiplicity-label:{relation_id}:{label_index}'))
                label_item.set('text', label['text'])
                label_item.set('modelElement', label['end_id'])
                saved_position = label_positions.get('source' if label['near_start'] else 'target')
                if isinstance(saved_position, dict):
                    x, y = saved_position.get('x', 0), saved_position.get('y', 0)
                    width, height = max(6, len(label['text']) * 7), 14
                else:
                    x, y, width, height = _label_bounds(route[0], route[-1], label['text'], label['near_start'])
                bounds = etree.SubElement(label_item, 'bounds')
                bounds.set(f'{{{XMI}}}type', 'dc:bounds')
                bounds.set(f'{{{XMI}}}id', _ea_id(f'multiplicity-bounds:{relation_id}:{label_index}'))
                bounds.set('x', str(x)); bounds.set('y', str(y))
                bounds.set('width', str(width)); bounds.set('height', str(height))
            if data.get('umlLabel'):
                label_item = etree.SubElement(edge, 'ownedElement')
                label_item.set(f'{{{XMI}}}type', 'umldi:UMLNameLabel')
                label_item.set(f'{{{XMI}}}id', _ea_id(f'relation-label:{relation_id}'))
                label_item.set('text', str(data['umlLabel']))
                saved_position = label_positions.get('name')
                if isinstance(saved_position, dict):
                    x, y = saved_position.get('x', 0), saved_position.get('y', 0)
                    width, height = max(20, len(str(data['umlLabel'])) * 7), 14
                else:
                    x, y, width, height = _label_bounds(route[0], route[-1], str(data['umlLabel']), True)
                bounds = etree.SubElement(label_item, 'bounds')
                bounds.set(f'{{{XMI}}}type', 'dc:bounds')
                bounds.set(f'{{{XMI}}}id', _ea_id(f'relation-bounds:{relation_id}'))
                bounds.set('x', str(x)); bounds.set('y', str(y + 18))
                bounds.set('width', str(width)); bounds.set('height', str(height))
            for waypoint_index, (x, y) in enumerate(route):
                waypoint = etree.SubElement(edge, 'waypoint')
                waypoint.set(f'{{{XMI}}}type', 'dc:waypoint')
                waypoint.set(f'{{{XMI}}}id', _ea_id(f'waypoint:{relation_id}:{waypoint_index}'))
                waypoint.set('x', str(x)); waypoint.set('y', str(y))

    # Enterprise Architect registers diagrams in this extension.  The package
    # metadata is required before the diagram entry; local database IDs are
    # intentionally omitted so EA allocates them during import.
    extension = etree.SubElement(root, f'{{{XMI}}}Extension')
    extension.set('extender', 'Enterprise Architect')
    extension.set('extenderID', '6.5')
    extension_elements = etree.SubElement(extension, 'elements')
    package_metadata = etree.SubElement(extension_elements, 'element')
    package_metadata.set(f'{{{XMI}}}idref', package_id)
    package_metadata.set(f'{{{XMI}}}type', 'uml:Package')
    package_metadata.set('name', 'DiagramCraft')
    package_metadata.set('scope', 'public')
    package_model = etree.SubElement(package_metadata, 'model')
    package_model.set('package2', package_model_id)
    package_model.set('package', model_id)
    package_model.set('tpos', '1')
    package_model.set('ea_localid', '1')
    package_model.set('ea_eleType', 'package')
    package_properties = etree.SubElement(package_metadata, 'properties')
    package_properties.set('isSpecification', 'false')
    package_properties.set('sType', 'Package')
    package_properties.set('nType', '0')
    package_properties.set('scope', 'public')
    etree.SubElement(package_metadata, 'code')
    package_project = etree.SubElement(package_metadata, 'project')
    package_project.set('author', 'DiagramCraft')
    package_project.set('version', '1.0')
    package_project.set('created', '2026-01-01 00:00:00')
    package_project.set('modified', '2026-01-01 00:00:00')
    style = etree.SubElement(package_metadata, 'style')
    style.set('appearance', 'BackColor=-1;BorderColor=-1;BorderWidth=-1;FontColor=-1;')
    etree.SubElement(package_metadata, 'tags')
    etree.SubElement(package_metadata, 'xrefs')
    extended = etree.SubElement(package_metadata, 'extendedProperties')
    extended.set('tagged', '0')
    extended.set('package_name', 'EA_Model')
    package_properties = etree.SubElement(package_metadata, 'packageproperties')
    package_properties.set('version', '1.0')
    package_properties.set('tpos', '1')
    etree.SubElement(package_metadata, 'paths')
    package_times = etree.SubElement(package_metadata, 'times')
    package_times.set('created', '2026-01-01 00:00:00')
    package_times.set('modified', '2026-01-01 00:00:00')
    flags = etree.SubElement(package_metadata, 'flags')
    flags.set('iscontrolled', 'FALSE')
    flags.set('isprotected', 'FALSE')

    # EA's extension mirrors every UML classifier.  UMLDI alone describes
    # drawing shapes, but these records let EA register the elements in the
    # package before resolving the diagram and its connectors.
    for local_id, (node, xmi_id) in enumerate(diagram_nodes, start=2):
        data = node.get('data') or {}
        kind = data.get('kind', 'class')
        classifier = etree.SubElement(extension_elements, 'element')
        classifier.set(f'{{{XMI}}}idref', xmi_id)
        classifier.set(f'{{{XMI}}}type', 'uml:Interface' if kind == 'interface' else 'uml:Class')
        classifier.set('name', str(data.get('title', 'Tipo')).removesuffix('.java'))
        classifier.set('scope', 'public')
        classifier_model = etree.SubElement(classifier, 'model')
        classifier_model.set('package', package_id)
        classifier_model.set('tpos', '0')
        classifier_model.set('ea_localid', str(local_id))
        classifier_model.set('ea_eleType', 'element')
        classifier_properties = etree.SubElement(classifier, 'properties')
        classifier_properties.set('isSpecification', 'false')
        classifier_properties.set('sType', 'Interface' if kind == 'interface' else 'Class')
        classifier_properties.set('nType', '0')
        classifier_properties.set('scope', 'public')
        classifier_properties.set('isAbstract', str(bool(data.get('abstract'))).lower())
        etree.SubElement(classifier, 'code')
        classifier_project = etree.SubElement(classifier, 'project')
        classifier_project.set('author', 'DiagramCraft')
        classifier_project.set('version', '1.0')
        classifier_project.set('created', '2026-01-01 00:00:00')
        classifier_project.set('modified', '2026-01-01 00:00:00')
        classifier_style = etree.SubElement(classifier, 'style')
        classifier_style.set('appearance', 'BackColor=-1;BorderColor=-1;BorderWidth=-1;FontColor=-1;')
        etree.SubElement(classifier, 'tags')
        etree.SubElement(classifier, 'xrefs')
        classifier_extended = etree.SubElement(classifier, 'extendedProperties')
        classifier_extended.set('tagged', '0')
        classifier_extended.set('package_name', 'DiagramCraft')
        attributes = etree.SubElement(classifier, 'attributes')
        for attribute_index, attribute in enumerate(attribute_records.get(xmi_id, []), start=1):
            ea_attribute = etree.SubElement(attributes, 'attribute')
            ea_attribute.set(f'{{{XMI}}}idref', attribute['id'])
            ea_attribute.set('name', attribute['name'])
            ea_attribute.set('scope', attribute['visibility'].title())
            etree.SubElement(ea_attribute, 'initial')
            etree.SubElement(ea_attribute, 'documentation')
            ea_attribute_model = etree.SubElement(ea_attribute, 'model')
            ea_attribute_model.set('ea_localid', str(local_id * 1000 + attribute_index))
            ea_attribute_model.set('ea_guid', _ea_guid(attribute['id']))
            ea_attribute_properties = etree.SubElement(ea_attribute, 'properties')
            ea_attribute_properties.set('type', attribute['type'])
            ea_attribute_properties.set('collection', 'false')
            ea_attribute_properties.set('duplicates', '0')
            ea_attribute_properties.set('changeability', 'changeable')
            etree.SubElement(ea_attribute, 'coords').set('ordered', '0')
            containment = etree.SubElement(ea_attribute, 'containment')
            containment.set('containment', 'Not Specified')
            containment.set('position', str(attribute_index))
            etree.SubElement(ea_attribute, 'stereotype')
            bounds = etree.SubElement(ea_attribute, 'bounds')
            bounds.set('lower', '1')
            bounds.set('upper', '1')
            etree.SubElement(ea_attribute, 'options')
            etree.SubElement(ea_attribute, 'style')
            etree.SubElement(ea_attribute, 'styleex').set('value', 'volatile=0;')
            etree.SubElement(ea_attribute, 'tags')
            etree.SubElement(ea_attribute, 'xrefs')

    connectors = etree.SubElement(extension, 'connectors')
    connector_types = {
        'asociacion': 'Association', 'agregacion': 'Aggregation',
        'composicion': 'Composition', 'herencia': 'Generalization',
        'realizacion': 'Realisation', 'dependencia': 'Dependency',
    }
    for connector_local_id, (relation_id, source, target, data, _) in enumerate(
        diagram_edges, start=len(diagram_nodes) + 2,
    ):
        relation = etree.SubElement(connectors, 'connector')
        relation.set(f'{{{XMI}}}idref', relation_id)
        relation_type = data.get('relationType', 'asociacion')
        whole = ids.get(data.get('wholeNodeId'))
        for endpoint, endpoint_name, multiplicity_key in (
            (source, 'source', 'multiplicidadOrigen'),
            (target, 'target', 'multiplicidadDestino'),
        ):
            endpoint_element = etree.SubElement(relation, endpoint_name)
            endpoint_element.set(f'{{{XMI}}}idref', endpoint)
            classifier = model_items[endpoint]
            endpoint_model = etree.SubElement(endpoint_element, 'model')
            endpoint_model.set('type', 'Interface' if classifier.get(f'{{{XMI}}}type') == 'uml:Interface' else 'Class')
            endpoint_model.set('name', classifier.get('name', 'Tipo'))
            endpoint_role = etree.SubElement(endpoint_element, 'role')
            endpoint_role.set('visibility', 'Public')
            endpoint_role.set('targetScope', 'instance')
            endpoint_type = etree.SubElement(endpoint_element, 'type')
            values = _multiplicity_values(data.get(multiplicity_key))
            if values is not None:
                endpoint_type.set('multiplicity', values[2])
            endpoint_type.set(
                'aggregation',
                ('composite' if relation_type == 'composicion' else 'shared')
                if endpoint == whole else 'none',
            )
            endpoint_type.set('containment', 'Unspecified')
            etree.SubElement(endpoint_element, 'constraints')
            modifiers = etree.SubElement(endpoint_element, 'modifiers')
            modifiers.set('isOrdered', 'false')
            modifiers.set('changeable', 'none')
            navigable = data.get('sourceNavigable' if endpoint_name == 'source' else 'targetNavigable')
            if isinstance(navigable, bool):
                modifiers.set('isNavigable', str(navigable).lower())
            etree.SubElement(endpoint_element, 'style').set(
                'value', 'Union=0;Derived=0;AllowDuplicates=0;Owned=0;Navigable=Unspecified;'
            )
            etree.SubElement(endpoint_element, 'documentation')
            etree.SubElement(endpoint_element, 'xrefs')
            etree.SubElement(endpoint_element, 'tags')
        connector_model = etree.SubElement(relation, 'model')
        connector_model.set('ea_localid', str(connector_local_id))
        properties = etree.SubElement(relation, 'properties')
        properties.set('ea_type', connector_types.get(relation_type, 'Association'))
        properties.set('direction', 'Unspecified')
        modifiers = etree.SubElement(relation, 'modifiers')
        modifiers.set('isRoot', 'false')
        modifiers.set('isLeaf', 'false')
        etree.SubElement(relation, 'style')
        etree.SubElement(relation, 'labels')
        etree.SubElement(relation, 'extendedProperties')
        etree.SubElement(relation, 'tags')
        etree.SubElement(relation, 'xrefs')

    etree.SubElement(extension, 'profiles')
    diagrams = etree.SubElement(extension, 'diagrams')
    ea_diagram = etree.SubElement(diagrams, 'diagram')
    # EA uses its diagram GUID in both the UMLDI representation and its
    # extension registry.  The shared identifier is how EA attaches the
    # Project Browser diagram entry to the visible UMLDI drawing.
    ea_diagram.set(f'{{{XMI}}}id', ea_diagram_id)
    diagram_model = etree.SubElement(ea_diagram, 'model')
    diagram_model.set('package', package_id)
    diagram_model.set('owner', package_id)
    diagram_model.set('localID', str(len(diagram_nodes) + len(diagram_edges) + 2))
    diagram_properties = etree.SubElement(ea_diagram, 'properties')
    diagram_properties.set('name', diagram_name)
    diagram_properties.set('type', 'Logical')
    project = etree.SubElement(ea_diagram, 'project')
    project.set('author', 'DiagramCraft')
    project.set('version', '1.0')
    project.set('created', '2026-01-01 00:00:00')
    project.set('modified', '2026-01-01 00:00:00')
    etree.SubElement(ea_diagram, 'style1').set(
        'value', 'ShowPrivate=1;ShowProtected=1;ShowPublic=1;HideRelationships=0;'
        'ShowDetails=0;ShowIcons=1;ConnectorNotation=UML 2.1;'
    )
    etree.SubElement(ea_diagram, 'style2').set(
        'value', 'HideQuals=0;ShowNotes=0;VisibleAttributeDetail=0;ShowOpRetType=1;'
    )
    etree.SubElement(ea_diagram, 'swimlanes').set('value', 'locked=false;orientation=0;width=0;')
    etree.SubElement(ea_diagram, 'matrixitems').set('value', 'locked=false;matrixactive=false;')
    etree.SubElement(ea_diagram, 'extendedProperties')
    diagram_elements = etree.SubElement(ea_diagram, 'elements')
    for index, (_, xmi_id) in enumerate(diagram_nodes, start=1):
        x, y, width, height = node_bounds[xmi_id]
        element = etree.SubElement(diagram_elements, 'element')
        element.set('geometry', f'Left={x};Top={y};Right={x + width};Bottom={y + height};')
        element.set('subject', xmi_id)
        element.set('seqno', str(index))
        element.set('style', f'ImageID=0;DUID={diagram_object_ids[xmi_id]};')
    for relation_index, (relation_id, source, target, _, _) in enumerate(diagram_edges, start=1):
        element = etree.SubElement(diagram_elements, 'element')
        element.set('geometry', 'SX=0;SY=0;EX=0;EY=0;EDGE=3;$LLB=;LLT=;LMT=;LMB=;LRT=;LRB=;IRHS=;ILHS=;Path=;')
        element.set('subject', relation_id)
        element.set('seqno', str(len(diagram_nodes) + relation_index))
        element.set(
            'style',
            f'Mode=3;EOID={diagram_object_ids[target]};SOID={diagram_object_ids[source]};'
            'Color=-1;LWidth=0;Hidden=0;',
        )

    return etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True)
