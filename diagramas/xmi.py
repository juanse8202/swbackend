"""Small, secure UML 2.5 XMI interchange adapter.

The adapter deliberately supports the portable UML core emitted by this
project.  EA extension blocks are detected and reported, never guessed.
"""
from __future__ import annotations

from lxml import etree

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
    return '*' if upper == '*' and lower == '0' else ('1..*' if upper == '*' and lower == '1' else (upper if lower == upper else f'{lower}..{upper}'))


def _end_type(end):
    """Read both portable UML attributes and EA's nested <type xmi:idref>."""
    if end.get('type'):
        return end.get('type')
    type_element = next((item for item in end if _local(item) == 'type'), None)
    if type_element is None:
        return None
    return _attr(type_element, 'idref') or (type_element.get('href') or '').rsplit('#', 1)[-1] or None


def _warning(code, message, *, xmi_id=None):
    return {'code': code, 'message': message, **({'xmi_id': xmi_id} if xmi_id else {})}


def _layout(index):
    return {'x': 120 + (index % 4) * 320, 'y': 100 + (index // 4) * 210}


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
                    attr_type = _end_type(child) or 'String'
                    if child.get(f'{{{DC}}}id') == 'true':
                        attr_type = f'{attr_type} (@Id)'
                    attributes.append({'visibility': child.get('visibility', '+'), 'name': child.get('name', 'atributo'), 'type': attr_type})
                elif _local(child) == 'ownedOperation':
                    methods.append(f"{child.get('name', 'operacion')}(): {child.get('type', 'void')}")
                elif _local(child) == 'generalization':
                    relation_records.append(('herencia', xmi_id, child.get('general'), {}))
                elif _local(child) == 'interfaceRealization':
                    relation_records.append(('realizacion', xmi_id, child.get('contract'), {}))
            node_id = f'xmi-{xmi_id}'
            id_map[xmi_id] = node_id
            nodes.append({'id': node_id, 'type': 'umlClass', 'position': _layout(len(nodes)), 'data': {'kind': node_kind, 'title': f'{title}.java', 'abstract': element.get('isAbstract') == 'true', 'properties': attributes, 'methods': methods, 'xmiId': xmi_id}})
        elif kind in {'uml:Association', 'uml:Dependency'}:
            relation_records.append((kind, xmi_id, element, {}))

    for relation_type, first, second, _ in relation_records:
        if relation_type in {'herencia', 'realizacion'}:
            if first not in id_map or second not in id_map:
                warnings.append(_warning('unresolved_reference', f'Se omitió {relation_type} con referencia no resuelta.', xmi_id=first))
                continue
            edges.append({'id': f'xmi-rel-{len(edges)}', 'source': id_map[first], 'target': id_map[second], 'type': 'relationEdge', 'data': {'relationType': relation_type, 'multiplicidadOrigen': '', 'multiplicidadDestino': '', 'xmiImported': True, 'xmiRelationId': first}})
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
            if whole_index is not None:
                data['wholeNodeId'] = id_map.get(_end_type(ends[whole_index]))
        if source not in id_map or target not in id_map:
            warnings.append(_warning('unresolved_reference', f'Se omitió {type_name} con referencia no resuelta.', xmi_id=first))
            continue
        data['xmiRelationId'] = first
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
            key = (
                edge['data'].get('xmiRelationId'), edge['source'], edge['target'], edge['data'].get('relationType'),
                edge['data'].get('multiplicidadOrigen'), edge['data'].get('multiplicidadDestino'),
            )
            if key not in edge_keys:
                edge_keys.add(key)
                unique_edges.append(edge)
        edges = unique_edges
    return {'nodes': nodes, 'edges': edges, 'report': {'dialect': dialect, 'xmi_version': version, 'exporter': exporter, 'imported': {'nodes': len(nodes), 'edges': len(edges)}, 'warnings': warnings, 'omitted': warnings, 'errors': []}}


def _bounds_for_node(node, index):
    """Return deterministic EA/UMLDI bounds from React Flow coordinates."""
    position = node.get('position') or _layout(index)
    x = int(float(position.get('x', _layout(index)['x'])))
    y = int(float(position.get('y', _layout(index)['y'])))
    data = node.get('data') or {}
    width = int(float(node.get('width') or 220))
    height = int(float(node.get('height') or max(90, 58 + 20 * len(data.get('properties') or []))))
    return x, y, width, height


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


def export_xmi(nodes, edges):
    """Export UML semantics plus an EA-readable UMLDI class diagram."""
    root = etree.Element(f'{{{XMI}}}XMI', nsmap=NS)
    root.set(f'{{{XMI}}}version', '2.5')
    root.set(f'{{{DC}}}exporter', 'DiagramCraft UML 2.5.1 core')
    documentation = etree.SubElement(root, f'{{{XMI}}}Documentation')
    documentation.set('exporter', 'Enterprise Architect')
    documentation.set('exporterVersion', '6.5')
    model = etree.SubElement(root, f'{{{UML}}}Model')
    model.set(f'{{{XMI}}}type', 'uml:Model')
    model.set(f'{{{XMI}}}id', 'dc-root-model')
    model.set('name', 'EA_Model')
    package = etree.SubElement(model, 'packagedElement')
    package.set(f'{{{XMI}}}type', 'uml:Package')
    package.set(f'{{{XMI}}}id', 'dc-package-diagramcraft')
    package.set('name', 'DiagramCraft')

    ids, model_items, diagram_nodes = {}, {}, []
    for index, node in enumerate(nodes):
        data, node_id = node.get('data') or {}, node.get('id')
        kind = data.get('kind', 'entity')
        if kind not in {'entity', 'class', 'interface'}:
            continue
        # xmiId is provenance from an imported EA model. Reusing it when the
        # user exports back into the same EA project collides with the source
        # classifier (for example Finanzas.auth_user).  An export owns a fresh
        # namespace, while DiagramCraft's node id remains the stable mapping.
        xmi_id = f'dc-node-{index}'
        ids[node_id] = xmi_id
        item = etree.SubElement(package, 'packagedElement')
        item.set(f'{{{XMI}}}type', 'uml:Interface' if kind == 'interface' else 'uml:Class')
        item.set(f'{{{XMI}}}id', xmi_id)
        item.set('name', str(data.get('title', 'Tipo')).removesuffix('.java'))
        item.set(f'{{{DC}}}kind', kind)
        if data.get('abstract'):
            item.set('isAbstract', 'true')
        for attribute in data.get('properties', []):
            child = etree.SubElement(item, 'ownedAttribute')
            child.set('name', str(attribute.get('name', 'atributo')))
            child.set('type', str(attribute.get('type', 'String')).replace(' (@Id)', ''))
            child.set('visibility', str(attribute.get('visibility', '+')))
            if '(@Id)' in str(attribute.get('type', '')):
                child.set(f'{{{DC}}}id', 'true')
        for method in data.get('methods', []):
            child = etree.SubElement(item, 'ownedOperation')
            child.set('name', str(method).split('(')[0].strip() or 'operacion')
            child.set('type', str(method).split(':')[-1].strip() if ':' in str(method) else 'void')
        model_items[xmi_id] = item
        diagram_nodes.append((node, xmi_id))

    diagram_edges = []
    for index, edge in enumerate(edges):
        data = edge.get('data') or {}
        source, target = ids.get(edge.get('source')), ids.get(edge.get('target'))
        if not source or not target:
            continue
        rel = data.get('relationType', 'asociacion')
        relation_id = f'dc-rel-{index}'
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
            if rel == 'dependencia':
                semantic.set(f'{{{XMI}}}type', 'uml:Dependency')
                semantic.set('client', source)
                semantic.set('supplier', target)
            else:
                semantic.set(f'{{{XMI}}}type', 'uml:Association')
                if data.get('umlLabel'):
                    semantic.set('name', str(data['umlLabel']))
                whole = ids.get(data.get('wholeNodeId'))
                end_ids = []
                for end_index in range(2):
                    end_id = f'dc-end-{index}-{end_index}'
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

    diagram = etree.SubElement(root, f'{{{UMLDI}}}Diagram')
    diagram.set(f'{{{XMI}}}type', 'umldi:UMLClassDiagram')
    diagram.set(f'{{{XMI}}}id', 'dc-diagram-0')
    diagram.set('name', 'DiagramCraft')
    diagram.set('isFrame', 'false')
    diagram.set('modelElement', 'dc-package-diagramcraft')
    node_bounds = {
        xmi_id: _bounds_for_node(node, index)
        for index, (node, xmi_id) in enumerate(diagram_nodes)
    }
    for index, (node, xmi_id) in enumerate(diagram_nodes):
        shape = etree.SubElement(diagram, 'ownedElement')
        shape.set(f'{{{XMI}}}type', 'umldi:UMLClassifierShape')
        shape.set(f'{{{XMI}}}id', f'dc-shape-{index}')
        shape.set('modelElement', xmi_id)
        name = etree.SubElement(shape, 'ownedElement')
        name.set(f'{{{XMI}}}type', 'umldi:UMLNameLabel')
        name.set(f'{{{XMI}}}id', f'dc-name-{index}')
        name.set('text', str((node.get('data') or {}).get('title', 'Tipo')).removesuffix('.java'))
        x, y, width, height = node_bounds[xmi_id]
        bounds = etree.SubElement(shape, 'bounds')
        bounds.set(f'{{{XMI}}}type', 'dc:bounds')
        bounds.set(f'{{{XMI}}}id', f'dc-bounds-{index}')
        bounds.set('x', str(x)); bounds.set('y', str(y))
        bounds.set('width', str(width)); bounds.set('height', str(height))
    for index, (relation_id, source, target, data, multiplicity_labels) in enumerate(diagram_edges):
        edge = etree.SubElement(diagram, 'ownedElement')
        edge.set(f'{{{XMI}}}type', 'umldi:UMLEdge')
        edge.set(f'{{{XMI}}}id', f'dc-edge-{index}')
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
            for label_index, label in enumerate(multiplicity_labels):
                label_item = etree.SubElement(edge, 'ownedElement')
                label_item.set(f'{{{XMI}}}type', 'umldi:UMLMultiplicityLabel')
                label_item.set(f'{{{XMI}}}id', f'dc-multiplicity-label-{index}-{label_index}')
                label_item.set('text', label['text'])
                label_item.set('modelElement', label['end_id'])
                x, y, width, height = _label_bounds(start, finish, label['text'], label['near_start'])
                bounds = etree.SubElement(label_item, 'bounds')
                bounds.set(f'{{{XMI}}}type', 'dc:bounds')
                bounds.set(f'{{{XMI}}}id', f'dc-multiplicity-bounds-{index}-{label_index}')
                bounds.set('x', str(x)); bounds.set('y', str(y))
                bounds.set('width', str(width)); bounds.set('height', str(height))
            if data.get('umlLabel'):
                label_item = etree.SubElement(edge, 'ownedElement')
                label_item.set(f'{{{XMI}}}type', 'umldi:UMLNameLabel')
                label_item.set(f'{{{XMI}}}id', f'dc-relation-label-{index}')
                label_item.set('text', str(data['umlLabel']))
                x, y, width, height = _label_bounds(start, finish, str(data['umlLabel']), True)
                bounds = etree.SubElement(label_item, 'bounds')
                bounds.set(f'{{{XMI}}}type', 'dc:bounds')
                bounds.set(f'{{{XMI}}}id', f'dc-relation-bounds-{index}')
                bounds.set('x', str(x)); bounds.set('y', str(y + 18))
                bounds.set('width', str(width)); bounds.set('height', str(height))
            for waypoint_index, (x, y) in enumerate((start, finish)):
                waypoint = etree.SubElement(edge, 'waypoint')
                waypoint.set(f'{{{XMI}}}type', 'dc:waypoint')
                waypoint.set(f'{{{XMI}}}id', f'dc-waypoint-{index}-{waypoint_index}')
                waypoint.set('x', str(x)); waypoint.set('y', str(y))

    # Enterprise Architect registers diagrams in this extension.  The package
    # metadata is required before the diagram entry; local database IDs are
    # intentionally omitted so EA allocates them during import.
    extension = etree.SubElement(root, f'{{{XMI}}}Extension')
    extension.set('extender', 'Enterprise Architect')
    extension.set('extenderID', '6.5')
    extension_elements = etree.SubElement(extension, 'elements')
    package_metadata = etree.SubElement(extension_elements, 'element')
    package_metadata.set(f'{{{XMI}}}idref', 'dc-package-diagramcraft')
    package_metadata.set(f'{{{XMI}}}type', 'uml:Package')
    package_metadata.set('name', 'DiagramCraft')
    package_metadata.set('scope', 'public')
    package_model = etree.SubElement(package_metadata, 'model')
    package_model.set('package2', 'dc-package-diagramcraft')
    package_model.set('package', 'dc-root-model')
    package_model.set('tpos', '1')
    package_model.set('ea_eleType', 'package')
    package_properties = etree.SubElement(package_metadata, 'properties')
    package_properties.set('isSpecification', 'false')
    package_properties.set('sType', 'Package')
    package_properties.set('nType', '0')
    package_properties.set('scope', 'public')
    etree.SubElement(package_metadata, 'code')
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
    flags = etree.SubElement(package_metadata, 'flags')
    flags.set('iscontrolled', 'FALSE')
    flags.set('isprotected', 'FALSE')

    etree.SubElement(extension, 'connectors')
    etree.SubElement(extension, 'profiles')
    diagrams = etree.SubElement(extension, 'diagrams')
    ea_diagram = etree.SubElement(diagrams, 'diagram')
    # EA uses its diagram GUID in both the UMLDI representation and its
    # extension registry.  The shared identifier is how EA attaches the
    # Project Browser diagram entry to the visible UMLDI drawing.
    ea_diagram.set(f'{{{XMI}}}id', 'dc-diagram-0')
    diagram_model = etree.SubElement(ea_diagram, 'model')
    diagram_model.set('package', 'dc-package-diagramcraft')
    diagram_model.set('owner', 'dc-package-diagramcraft')
    diagram_properties = etree.SubElement(ea_diagram, 'properties')
    diagram_properties.set('name', 'DiagramCraft')
    diagram_properties.set('type', 'Logical')
    diagram_elements = etree.SubElement(ea_diagram, 'elements')
    for index, (_, xmi_id) in enumerate(diagram_nodes, start=1):
        x, y, width, height = node_bounds[xmi_id]
        element = etree.SubElement(diagram_elements, 'element')
        element.set('geometry', f'Left={x};Top={y};Right={x + width};Bottom={y + height};')
        element.set('subject', xmi_id)
        element.set('seqno', str(index))
        element.set('style', 'ImageID=0;')
    for relation_index, (relation_id, _, _, _, _) in enumerate(diagram_edges, start=1):
        element = etree.SubElement(diagram_elements, 'element')
        element.set('geometry', 'SX=0;SY=0;EX=0;EY=0;EDGE=3;Path=;')
        element.set('subject', relation_id)
        element.set('seqno', str(len(diagram_nodes) + relation_index))
        element.set('style', 'Mode=3;Color=-1;LWidth=0;Hidden=0;')

    return etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=True)
