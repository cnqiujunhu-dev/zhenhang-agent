"""Admin-only policy configuration; keys, arbitrary endpoints and banking execution are excluded."""
from fastapi import Depends, Request
from pydantic import Field

from .access_models import AccessAudit
from .accounts import require_account_role
from .contracts import DomainError, StrictModel
from .model_routing import RoutingPolicyConflict, read_routing_policy, update_routing_policy
from .routing_models import RoutingPolicy


class RoutingUpdate(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    policy: RoutingPolicy
    reason: str = Field(min_length=4, max_length=200)


def install_routing_routes(app, get_session, get_principal):
    def snapshot(session, gateway):
        return {"policy": read_routing_policy(session).model_dump(), "registry": gateway.registry_status()}

    @app.get("/api/v2/admin/model-routing")
    def read(request: Request, session=Depends(get_session), principal=Depends(get_principal)):
        require_account_role(session, principal, {"ADMIN"})
        return snapshot(session, request.app.state.model_gateway)

    @app.put("/api/v2/admin/model-routing")
    def update(payload: RoutingUpdate, request: Request, session=Depends(get_session), principal=Depends(get_principal)):
        admin = require_account_role(session, principal, {"ADMIN"})
        gateway = request.app.state.model_gateway
        if gateway.registry is None:
            raise DomainError("ROUTING_REGISTRY_UNAVAILABLE", "当前适配器未登记可切换模型", 503)
        try:
            updated = update_routing_policy(session, payload.expected_version, payload.policy, admin.id, gateway.registry)
        except RoutingPolicyConflict:
            raise DomainError("ROUTING_POLICY_VERSION_CONFLICT", "模型配置已变化，请刷新后修改", 409) from None
        except ValueError:
            raise DomainError("INVALID_ROUTING_POLICY", "只能选择已登记模型与有效路由预算", 422) from None
        session.add(AccessAudit(actor_account_id=admin.id, action="MODEL_ROUTING_POLICY_UPDATED",
            payload_json={"reason": payload.reason, "version": updated.version,
                          "mode": updated.mode, "cloud_enabled": updated.cloud_enabled}))
        session.commit()
        return {**snapshot(session, gateway), "changed": True}
