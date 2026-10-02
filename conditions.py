"""Evaluate YAML predicates. Unknown controllers never imply inapplicability."""
def resolve(state, path):
    node = state
    for part in path.split('.'):
        node = node.get(part) if isinstance(node, dict) else getattr(node, part, None)
        if node is None:
            break
    return node

def matches(condition, state):
    if not condition:
        return True
    if 'all' in condition:
        return all(matches(child, state) for child in condition['all'])
    if 'any' in condition:
        return any(matches(child, state) for child in condition['any'])
    value = resolve(state, condition['field'])
    status = getattr(value, 'status', None)
    operator = condition['operator']
    if operator == 'missing':
        return value is None or status == 'missing'
    if operator == 'unresolved':
        return value is None or status in {'missing', 'unconfirmed'}
    if operator == 'answered':
        return status == 'answered'
    if operator == 'equals':
        return value == condition['value']
    if operator == 'greater_than_or_equal':
        return value is not None and value >= condition['value']
    raise ValueError(f'Unsupported operator: {operator}')

def leaves(condition):
    if not condition:
        return []
    for key in ('all', 'any'):
        if key in condition:
            return [leaf for child in condition[key] for leaf in leaves(child)]
    return [condition] if condition else []

def answer_fields(condition):
    return {leaf['field'].split('.')[1] for leaf in leaves(condition)
            if leaf['field'].startswith('answers.')}
