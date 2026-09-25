"""Compatibility helpers for legacy SQL foreign-key labels in UML JSON.

The document stored in ``Diagrama.nodes``/``edges`` remains authoritative.
These helpers only make an old, presentation-oriented property label explicit
UML metadata; they never read or write the legacy relational UML tables.
"""
from __future__ import annotations

from copy import deepcopy
import re


LEGACY_FOREIGN_KEY = re.compile(
    # EA/XMI imports in the wild use both ``FK: table`` and ``FK; table``.
    # The delimiter is descriptive SQL metadata, not part of the Java name.
    # Some EA-derived labels also carry one stray closing brace after the
    # metadata block. Accept only that exact legacy suffix; arbitrary invalid
    # Java member names must still be rejected by the normal validator.
    r'^\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*FK\s*[:;]\s*(?P<table>[^)]+?)\s*\)\}?\s*$',
    re.IGNORECASE,
)


def java_identifier(value):
    """Convert a SQL-ish field label to lower camel case deterministically."""
    parts = [part for part in re.split(r'[^A-Za-z0-9_$]+', str(value or '')) if part]
    if not parts:
        return 'foreignKey'
    first = parts[0]
    return first[:1].lower() + first[1:] + ''.join(part[:1].upper() + part[1:] for part in parts[1:])


def _node_table_names(node):
    data = node.get('data') or {}
    values = [data.get('sqlTable'), data.get('foreignTable'), data.get('title'), data.get('nombre')]
    return {
        str(value).removesuffix('.java').strip().lower()
        for value in values if isinstance(value, str) and value.strip()
    }


def _unique_foreign_key_name(base, used_names):
    """Keep both a scalar column and a legacy FK without duplicate Java fields."""
    if base not in used_names:
        return base
    candidate = f'{base}_id'
    suffix = 2
    while candidate in used_names:
        candidate = f'{base}_id_{suffix}'
        suffix += 1
    return candidate


def normalize_legacy_foreign_keys(nodes, edges, *, create_missing_relations=True):
    """Return a copied document with legacy ``field(FK: table)`` made explicit.

    A relation is added only when the referenced table can be matched to a
    node.  Otherwise the cleaned scalar property is retained and a structured
    warning lets the UI explain why no relation was invented.
    """
    normalized_nodes, normalized_edges = deepcopy(nodes), deepcopy(edges)
    by_table = {}
    for node in normalized_nodes:
        if isinstance(node, dict) and isinstance(node.get('id'), str):
            for table in _node_table_names(node):
                by_table.setdefault(table, node)

    warnings = []
    existing = {
        (edge.get('source'), edge.get('target'), (edge.get('data') or {}).get('sourceRole', ''))
        for edge in normalized_edges if isinstance(edge, dict)
        and (edge.get('data') or {}).get('relationType', 'asociacion') in {'asociacion', 'agregacion', 'composicion'}
    }
    edge_ids = {edge.get('id') for edge in normalized_edges if isinstance(edge, dict)}

    for node in normalized_nodes:
        if not isinstance(node, dict) or not isinstance(node.get('data'), dict):
            continue
        node_id, data = node.get('id'), node['data']
        properties = data.get('properties')
        if not isinstance(properties, list):
            continue
        # Reserve non-legacy property names before normalizing FK captions.
        # EA commonly has both ``deleted_at`` and ``deleted_at(FK; user)``.
        used_names = {
            attribute['name'] for attribute in properties
            if isinstance(attribute, dict) and isinstance(attribute.get('name'), str)
            and not LEGACY_FOREIGN_KEY.fullmatch(attribute['name'])
        }
        for index, attribute in enumerate(properties):
            if not isinstance(attribute, dict) or not isinstance(attribute.get('name'), str):
                continue
            match = LEGACY_FOREIGN_KEY.fullmatch(attribute['name'])
            if not match:
                continue
            original, field, table = attribute['name'], match.group('field'), match.group('table').strip()
            normalized_name = _unique_foreign_key_name(java_identifier(field), used_names)
            if normalized_name != java_identifier(field):
                warnings.append({
                    'code': 'legacy_foreign_key_name_disambiguated', 'element_id': node_id,
                    'attribute_name': normalized_name,
                    'message': f'La clave foránea {original!r} se normalizó como {normalized_name!r} para no duplicar un atributo Java.',
                })
            attribute['name'] = normalized_name
            used_names.add(normalized_name)
            attribute.setdefault('sourceLabel', original)
            attribute.setdefault('sqlForeignKey', True)
            attribute.setdefault('foreignTable', table)
            target = by_table.get(table.lower())
            if not target:
                attribute.setdefault('foreignKeyMode', 'scalar')
                warnings.append({
                    'code': 'unresolved_legacy_foreign_key', 'element_id': node_id,
                    'attribute_name': attribute['name'],
                    'message': f'No existe un nodo para la clave foránea {original!r}; se generará como campo escalar.',
                })
                continue
            target_id = target['id']
            if (not create_missing_relations
                    or target_id == node_id
                    or (node_id, target_id, attribute['name']) in existing):
                attribute.setdefault('foreignKeyMode', 'scalar')
                continue
            target_kind = (target.get('data') or {}).get('kind', 'entity')
            source_kind = data.get('kind', 'entity')
            edge_id = f'legacy-fk-{node_id}-{target_id}-{index}'
            suffix = 1
            base = edge_id
            while edge_id in edge_ids:
                suffix += 1
                edge_id = f'{base}-{suffix}'
            edge_ids.add(edge_id)
            normalized_edges.append({
                'id': edge_id, 'source': node_id, 'target': target_id, 'type': 'relationEdge',
                'data': {
                    'relationType': 'asociacion', 'sourceRole': attribute['name'],
                    'targetRole': '', 'multiplicidadOrigen': '*', 'multiplicidadDestino': '1',
                    'ownerNodeId': node_id,
                    'jpaManaged': source_kind == 'entity' and target_kind == 'entity',
                    'legacyForeignKey': True, 'sourceLabel': original, 'foreignTable': table,
                },
            })
            attribute['foreignKeyMode'] = 'relation'
            existing.add((node_id, target_id, attribute['name']))
    return normalized_nodes, normalized_edges, warnings
