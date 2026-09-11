"""Public, authorization-filtered discovery of each backend's filter contract."""
from .query_validation import field_family, field_operators, SCALAR_OPS, RANGE_OPS, TEXT_OPS


def filter_contract(product, decision, operation):
    postgres = (product.source.connector in {'postgres', 'postgresql'} if operation == 'query'
                else bool(product.retrieval and product.retrieval.backend == 'postgres'))
    search = operation != 'query' and not postgres
    fields = []
    for field in product.fields:
        if not field.filterable or field.name not in decision.allowed_fields - decision.masked_fields:
            continue
        family = field_family(field.data_type)
        ops = field_operators(field.data_type)
        if not postgres: ops.discard('array_is_empty')
        if not postgres and family == 'json': ops = {'exists'}
        if not postgres and not search and family == 'array': ops = {'exists'}
        fields.append({'field': field.name, 'data_type': field.data_type, 'operators': sorted(ops),
                       'json_path_operators': sorted(SCALAR_OPS | RANGE_OPS | TEXT_OPS) if postgres and family == 'json' else []})
    return {'dataset_id': product.id, 'dataset_version': product.version, 'operation': operation,
            'boolean_operators': ['and', 'or', 'not'], 'fields': fields,
            'limits': {'max_depth': 12, 'max_nodes': 256, 'max_list_values': 1000, 'max_filter_bytes': 65536},
            'pagination': 'keyset' if operation == 'query' else 'bounded_top_k',
            'null_semantics': 'SQL three-valued logic; only true matches'}
