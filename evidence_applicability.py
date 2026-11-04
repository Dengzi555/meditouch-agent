"""Match evidence scope against the original question and declared facts."""


def applicability_reason(record, question, context, *, demo=False):
    policy = record.get('applicability_policy')
    if policy is None and demo:
        return 'eligible'
    if not isinstance(policy, dict):
        return 'scope_missing'
    terms = policy.get('question_terms')
    populations = policy.get('populations', [])
    conditions = policy.get('conditions', {})
    if (not isinstance(terms, list) or not terms
            or any(not isinstance(term, str) or not term.strip() for term in terms)
            or not isinstance(populations, list)
            or any(not isinstance(value, str) or not value.strip() for value in populations)
            or not isinstance(conditions, dict)
            or any(not isinstance(key, str) or not key.strip() or not isinstance(value, str)
                   or not value.strip() for key, value in conditions.items())):
        return 'scope_invalid'
    if not any(term.strip().casefold() in question.casefold() for term in terms):
        return 'question_out_of_scope'
    if populations and context.get('population') not in populations:
        return 'population_not_established'
    if any(context.get(field) != value for field, value in conditions.items()):
        return 'condition_not_established'
    return 'eligible'
