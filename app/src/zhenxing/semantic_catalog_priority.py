"""Keep bill aggregates visible before exhausting the bounded entity budget.

Only the outgoing offer order changes. Full catalog contents, hashes, source
ordinals, money, and historical persisted offers remain untouched.
"""


def prioritize_catalog_entities(entities):
    bills=[row for row in entities if row.get('domain')=='BILLS']
    priorities={'bill_query_summary':0,'category_filter':1}
    ordered=iter(sorted(bills,key=lambda row:priorities.get(row.get('field'),2)))
    return [next(ordered) if row.get('domain')=='BILLS' else row for row in entities]
