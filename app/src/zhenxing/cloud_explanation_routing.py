"""Cloud-only candidates for explanation, never local planning or bank tools."""


def explanation_candidates(registry, policy):
    if registry is None or policy.mode == "LOCAL_ONLY" or not policy.cloud_enabled:
        return ()
    candidates = []
    for model in (policy.cloud_model, policy.cloud_secondary_model):
        candidate = registry.get(policy.cloud_provider, model) if model else None
        if candidate is not None and candidate.is_cloud and candidate.credential_configured and (
                callable(getattr(candidate.adapter, "_post_proposal", None)) and registry.circuit_allows(candidate)) and (
                (candidate.provider, candidate.model) not in {(row.provider, row.model) for row in candidates}):
            candidates.append(candidate)
    return tuple(candidates)
