"""Principal bindings are allowed only in administrator-owned row predicates."""
from copy import deepcopy

SCALARS = {'subject', 'tenant', 'client_id', 'agent_id'}


def bind_filter(expression, principal=None, *, validate_only=False):
    result = deepcopy(expression)
    def visit(node, depth=0):
        if not isinstance(node, dict): return
        if depth > 12: raise ValueError('filter complexity limit exceeded')
        for key in ('and','or'):
            if key in node:
                for child in node[key]: visit(child, depth+1)
                return
        if 'not' in node:
            visit(node['not'], depth+1)
            return
        value = node.get('value')
        if not isinstance(value, dict) or '$principal' not in value: return
        name = value.get('$principal')
        if set(value) != {'$principal'} or name not in SCALARS | {'groups'}:
            raise ValueError('unsupported principal binding')
        if name == 'groups':
            if node.get('op') not in {'in','not_in','array_overlaps','array_contains_all'}:
                raise ValueError('principal groups require a list operator')
            bound = ['validation'] if validate_only else sorted(principal.groups)
        else:
            bound = 'validation' if validate_only else getattr(principal, name)
        if not bound:
            raise ValueError('required principal attribute is missing')
        node['value'] = bound
    visit(result)
    return result
