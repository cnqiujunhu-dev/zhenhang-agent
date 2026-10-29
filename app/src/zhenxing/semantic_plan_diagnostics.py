"""Compilation reuses a cloud semantic receipt; never invent a second call."""
from dataclasses import dataclass
from copy import deepcopy


@dataclass(frozen=True)
class SemanticPlanDiagnostics:
    values:dict

    @classmethod
    def from_proof(cls,proof):
        binding=proof.binding
        receipt=binding['offered_context'].get('semantic_receipt')
        # Receipt itself is persisted on the original turn and cryptographically
        # source-bound by the private proof; upstream counters are separate.
        upstream=binding.get('semantic_diagnostics') or (receipt or {}).get('diagnostics') or {}
        return cls({'purpose':'COMPILE_VERIFIED_SEMANTIC_PROPOSAL','model_api_call_count':0,
            'primary_attempt_count':0,'upstream_semantic_model_api_call_count':1,
            'actual_provider':upstream.get('actual_provider','deepseek'),
            'actual_model':upstream.get('response_model') or upstream.get('actual_model') or proof.expected_proposal.model_label.split(':',1)[-1],
            'upstream_semantic_usage':deepcopy(upstream.get('usage') or {}),
            'upstream_semantic_context_wire':deepcopy(upstream.get('context_wire') or {}),
            'upstream_semantic_finish_reason':upstream.get('finish_reason'),
            'source_turn_id':binding['source_turn_id'],'source_binding_hash':binding['binding_hash'],
            'plan_source':'SEMANTIC_MODEL_PROPOSAL','fallback':False,'failure_reasons':[],
            'routing_policy_version':upstream.get('routing_policy_version')})

    def model_dump(self):return deepcopy(self.values)
