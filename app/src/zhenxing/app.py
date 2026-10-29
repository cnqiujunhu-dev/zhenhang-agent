from __future__ import annotations

from pathlib import Path
import json
import os
import time
from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import (
    CardView,
    CancelTaskRequest,
    ClarifyPayeeRequest,
    ClarifyTaskRequest,
    ConfirmTaskRequest,
    CreateTaskRequest,
    DemoSessionResponse,
    DomainError,
    PreferenceInput,
    TaskView,
)
from .database import create_database
from .model_gateway import ModelGateway
from .models import Account, AuditEvent, Holding, InvestmentProduct, OutboxMessage, Payee, Subscription, Task
from .platform import TOOL_REGISTRY, conversation_messages, list_preferences, save_preference
from .schema import ensure_schema
from .runtime_api import install_runtime_routes
from .reports_api import install_report_routes
from .banking_extensions import install_extension_routes
from .wealth_api import install_wealth_routes
from .extensions_api import install_extension_routes as install_personal_extension_routes
from .runtime_models import RuntimeRecord
from .resource_defaults import ARCHIVED_RESOURCE_STATUS
from uuid import uuid4
from .services import (
    Principal,
    analyze_bills,
    authenticate,
    cancel_task,
    clarify_task_payee,
    clarify_task,
    confirm_task_action,
    create_session,
    create_task,
    get_evidence,
    health_ready,
    list_cards,
    list_recent_tasks,
    owned_task,
    query_execution,
    task_view,
)


def create_app(database_url: str | None = None, gateway: ModelGateway | None = None,
               allow_legacy_commands: bool | None = None,
               bootstrap_accounts: bool | None = None) -> FastAPI:
    engine, session_factory = create_database(database_url)
    ensure_schema(engine)
    application = FastAPI(
        title="圳行Agent",
        version="0.21.0",
        description="面向银行业务的持久智能体工作流。",
    )
    application.state.session_factory = session_factory
    from .http_boundaries import RequestBoundaryMiddleware
    application.add_middleware(RequestBoundaryMiddleware)
    application.state.model_gateway = gateway or ModelGateway.from_environment()
    from .model_routing import initialize_routing_policy
    from .routing_models import RoutingPolicy
    with session_factory.begin() as setup:
        initialize_routing_policy(setup, RoutingPolicy(
            mode=os.getenv("ZX_ROUTING_MODE", "CLOUD_ONLY"),
            cloud_enabled=bool(os.getenv("ZX_DEEPSEEK_API_KEY")),
            cloud_model=os.getenv("ZX_DEEPSEEK_MODEL", "deepseek-flash")))
        should_seed = bootstrap_accounts if bootstrap_accounts is not None else (
            os.getenv("ZX_BOOTSTRAP_NAMED_ACCOUNTS", "0") == "1")
        if should_seed:
            from .accounts import bootstrap_named_accounts
            bootstrap_named_accounts(setup)
            from .asset_samples import bootstrap_asset_samples
            from .customer_profile_migration import seed_named_customer_profiles
            bootstrap_asset_samples(setup)
            seed_named_customer_profiles(setup)
    application.state.allow_legacy_commands = (os.getenv("ZX_ENABLE_LEGACY_COMMANDS", "0") == "1"
        if allow_legacy_commands is None else allow_legacy_commands)

    def legacy_command_guard():
        if not application.state.allow_legacy_commands:
            raise DomainError("USE_PERSISTENT_RUNTIME", "请使用 v2 持久任务接口", 410)

    @application.middleware("http")
    async def request_identity(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    def get_session(request: Request):
        with request.app.state.session_factory() as session:
            yield session

    def get_principal(
        session: Session = Depends(get_session),
        authorization: str | None = Header(default=None),
    ) -> Principal:
        return authenticate(session, authorization)

    def get_banking_principal(session: Session = Depends(get_session),
                              principal: Principal = Depends(get_principal)) -> Principal:
        from .permissions import require_banking_actor
        require_banking_actor(session, principal)
        return principal

    @application.exception_handler(RequestValidationError)
    def validation_error_handler(request: Request, exc: RequestValidationError):
        # FastAPI's default input echoes can include login passwords. Keep only
        # locations and finite validation categories, never submitted values.
        return JSONResponse(status_code=422, content={
            "request_id": getattr(request.state, "request_id", None),
            "detail": [{"loc": list(item["loc"]), "type": item["type"], "msg": "输入未通过格式校验"}
                       for item in exc.errors()]})

    @application.exception_handler(DomainError)
    def domain_error_handler(_request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "request_id": getattr(_request.state, "request_id", None),
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                }
            },
        )

    @application.get("/", include_in_schema=False)
    def index():
        ui = Path(__file__).parent / "static" / "ui" / "index.html"
        return FileResponse(ui if ui.exists() else Path(__file__).parent / "static" / "index.html",
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"})

    ui_dir = Path(__file__).parent / "static" / "ui"
    if ui_dir.exists():
        application.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    @application.get("/api/v1/health/live")
    def live():
        return {"status": "ok", "version": application.version}

    @application.get("/api/v1/health/ready")
    def ready(session: Session = Depends(get_session)):
        return {"status": "ready" if health_ready(session) else "unready", "database": "ok"}

    @application.get("/api/v2/health/ready")
    def runtime_ready(session: Session = Depends(get_session)):
        from .runtime_health import read_runtime_health
        result = read_runtime_health(session)
        return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)

    @application.get("/api/v2/platform/metrics")
    def runtime_status(session: Session = Depends(get_session), principal=Depends(get_principal)):
        from .runtime_health import runtime_metrics
        return runtime_metrics(session, principal)

    @application.get("/api/v2/platform/capabilities")
    def capabilities(session: Session = Depends(get_session), principal=Depends(get_principal)):
        from .capability_catalog import catalog
        return catalog(principal.role)

    @application.get("/api/v2/identity")
    def identity(session: Session = Depends(get_session), principal=Depends(get_principal)):
        return {"user_id": principal.user_id, "sandbox_id": principal.sandbox_id, "role": principal.role,
                "has_personal_bank_account": session.scalar(select(Account.id).where(
                    Account.sandbox_id == principal.sandbox_id, Account.user_id == principal.user_id)) is not None}

    @application.get("/api/v1/platform/status")
    def platform_status(request: Request):
        gateway = request.app.state.model_gateway
        return {
            "version": application.version,
            "model": gateway.primary.__class__.__name__,
            "model_status": gateway.provider_status() if hasattr(gateway, "provider_status") else None,
            "fallback": gateway.fallback.__class__.__name__,
            "runtime_version": "2.0",
            "legacy_commands_enabled": application.state.allow_legacy_commands,
            "registered_tools": TOOL_REGISTRY,
            "implemented_scenarios": ["BILL_ANALYSIS", "BILL_ANOMALY_LOCK_CARD", "TRANSFER", "CARD_MANAGEMENT", "SUBSCRIPTION_MANAGEMENT", "WEALTH", "CROSS_SCENARIO"],
            "extension_scenarios": ["FRAUD_ANALYSIS", "FAMILY_ASSISTANCE", "GOAL_CASHFLOW", "CARBON_BENEFITS"],
        }

    @application.post("/api/v1/demo/sessions", response_model=DemoSessionResponse)
    def new_demo_session(payload: dict | None = None, session: Session = Depends(get_session)):
        if os.getenv("ZX_ALLOW_DEMO_SESSIONS", "0") != "1":
            raise DomainError("DEMO_SESSIONS_DISABLED", "请使用已初始化的账户登录", 403)
        profile = (payload or {}).get("profile", "baseline")
        if profile not in {"baseline", "young_professional", "family", "pensioner", "investor"}:
            raise DomainError("INVALID_PROFILE", "请选择已有账套", 422)
        result = create_session(session, profile=profile)
        session.commit()
        return result

    @application.get("/api/v1/cards", response_model=list[CardView])
    def cards(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return list_cards(session, principal)

    @application.get("/api/v1/accounts")
    def accounts(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        rows = session.scalars(
            select(Account).where(
                Account.sandbox_id == principal.sandbox_id,
                Account.user_id == principal.user_id,
            )
        )
        from .extensions import reserved_goal_cash_minor
        result = []
        for item in rows:
            reserved = reserved_goal_cash_minor(session, principal.sandbox_id, principal.user_id, item.id)
            result.append({"account_id": item.id, "name": item.name, "balance_minor": item.balance_minor,
                           "version": item.version, "goal_reserved_minor": reserved,
                           "available_balance_minor": item.balance_minor - reserved})
        return result

    @application.get("/api/v1/payees")
    def payees(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        rows = session.scalars(
            select(Payee).where(
                Payee.sandbox_id == principal.sandbox_id,
                Payee.user_id == principal.user_id,
            )
        )
        from .internal_banking import recipient_scope, account_reference
        result = []
        for item in rows:
            scope = recipient_scope(session, item)
            result.append({"payee_id": item.id, "name": item.display_name, "account_last4": item.account_last4,
                           "transfer_channel": scope["transfer_channel"], "recipient_username": scope["recipient_username"],
                           "recipient_reference": account_reference(scope["recipient_account_id"]) if scope["recipient_account_id"] else None})
        return result

    @application.get("/api/v1/subscriptions")
    def subscriptions(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        from .subscription_timing import subscription_calendar_context, subscription_timing
        from .subscription_commerce import subscription_entitlement
        now, through = subscription_calendar_context(session, principal.sandbox_id)
        rows = session.scalars(select(Subscription).where(
            Subscription.sandbox_id == principal.sandbox_id,
            Subscription.user_id == principal.user_id,
            Subscription.status != ARCHIVED_RESOURCE_STATUS,
        ))
        return [{
            "subscription_id": item.id, "merchant": item.merchant,
            "amount_minor": item.amount_minor, "source_type": item.source_type,
            "status": item.status, "next_due_at": item.next_due_at.isoformat(),
            "version": item.version,
            **subscription_entitlement(item),
            **subscription_timing(item, calendar_time=now, data_through=through),
        } for item in rows]

    @application.get("/api/v1/investments")
    def investments(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        products = list(session.scalars(select(InvestmentProduct).where(
            InvestmentProduct.sandbox_id == principal.sandbox_id,
        )))
        holdings = {item.product_id: item for item in session.scalars(select(Holding).where(
            Holding.sandbox_id == principal.sandbox_id,
            Holding.user_id == principal.user_id,
        ))}
        return [{
            "product_id": item.id, "name": item.name,
            "risk_level": item.risk_level, "price_minor": item.price_minor,
            "status": item.status, "version": item.version,
            "holding_units": holdings[item.id].units if item.id in holdings else 0,
        } for item in products]

    @application.get("/api/v1/notifications")
    def notifications(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        from .customer_history_archive import visible_history
        def root_id(item):
            record = session.get(RuntimeRecord, item.task_id)
            return record.root_task_id if record and record.root_task_id else item.task_id
        rows = session.scalars(
            select(OutboxMessage)
            .join(Task, Task.id == OutboxMessage.task_id)
            .where(
                OutboxMessage.sandbox_id == principal.sandbox_id,
                Task.sandbox_id == principal.sandbox_id,
                Task.user_id == principal.user_id,
                OutboxMessage.status == "DELIVERED",
                visible_history(session, principal, Task.created_at),
            )
            .order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
            .limit(50)
        )
        return [{
            "notification_id": item.id,
            "task_id": item.task_id,
            "root_task_id": root_id(item),
            "event_type": item.event_type,
            "payload": item.payload_json,
            "created_at": item.created_at.isoformat(),
        } for item in rows]

    @application.get("/api/v1/conversations/current/messages")
    def messages(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return conversation_messages(
            session,
            sandbox_id=principal.sandbox_id,
            user_id=principal.user_id,
        )

    @application.get("/api/v1/preferences")
    def preferences(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return list_preferences(session, sandbox_id=principal.sandbox_id, user_id=principal.user_id)

    @application.put("/api/v1/preferences")
    def put_preference(
        payload: PreferenceInput,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        try:
            result = save_preference(
                session,
                sandbox_id=principal.sandbox_id,
                user_id=principal.user_id,
                key=payload.key,
                value=payload.value,
            )
        except ValueError as exc:
            raise DomainError("INVALID_PREFERENCE", str(exc), 422) from exc
        session.commit()
        return result

    @application.get("/api/v1/bills/summary")
    def bill_summary(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        account = session.scalar(
            select(Account).where(
                Account.sandbox_id == principal.sandbox_id,
                Account.user_id == principal.user_id,
            )
        )
        if account is None:
            raise DomainError("ACCOUNT_NOT_FOUND", "未找到当前账户", 404)
        from .financial_diagnostics import analyze_financial_request
        return analyze_financial_request(session, principal.sandbox_id, account.id, "分析账单")

    @application.post("/api/v1/tasks", response_model=TaskView)
    def post_task(
        payload: CreateTaskRequest,
        request: Request,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        legacy_command_guard()
        task = create_task(session, principal, payload.message, request.app.state.model_gateway)
        session.commit()
        return task_view(session, task)

    @application.get("/api/v1/tasks")
    def recent_tasks(
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return list_recent_tasks(session, principal)

    @application.get("/api/v1/tasks/{task_id}", response_model=TaskView)
    def get_task(
        task_id: str,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return task_view(session, owned_task(session, principal, task_id))

    @application.post("/api/v1/tasks/{task_id}/clarify-payee", response_model=TaskView)
    def clarify_payee(
        task_id: str,
        payload: ClarifyPayeeRequest,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        legacy_command_guard()
        task = clarify_task_payee(session, principal, task_id, payload)
        session.commit()
        return task_view(session, task)

    @application.post("/api/v1/tasks/{task_id}/clarify", response_model=TaskView)
    def clarify_open_task(
        task_id: str,
        payload: ClarifyTaskRequest,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        legacy_command_guard()
        task = clarify_task(session, principal, task_id, payload)
        session.commit()
        return task_view(session, task)

    @application.post("/api/v1/tasks/{task_id}/confirm", response_model=TaskView)
    def confirm_task(
        task_id: str,
        payload: ConfirmTaskRequest,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        legacy_command_guard()
        task = confirm_task_action(session, principal, task_id, payload, idempotency_key or "")
        session.commit()
        return task_view(session, task)

    @application.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskView)
    def cancel_open_task(
        task_id: str,
        payload: CancelTaskRequest,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        legacy_command_guard()
        task = cancel_task(session, principal, task_id, payload)
        session.commit()
        return task_view(session, task)

    @application.get("/api/v1/tasks/{task_id}/evidence")
    def evidence(
        task_id: str,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return get_evidence(session, principal, task_id)

    @application.get("/api/v1/tasks/{task_id}/executions/{idempotency_key}")
    def execution_status(
        task_id: str,
        idempotency_key: str,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
    ):
        return query_execution(session, principal, task_id, idempotency_key)

    @application.get("/api/v1/tasks/{task_id}/events")
    def task_events(
        task_id: str,
        request: Request,
        session: Session = Depends(get_session),
        principal: Principal = Depends(get_principal),
        last_event_id: int = 0,
    ):
        owned_task(session, principal, task_id)
        factory = request.app.state.session_factory

        def stream():
            last_seq = max(0, last_event_id)
            for _ in range(60):
                with factory() as poll:
                    events = list(
                        poll.scalars(
                            select(AuditEvent)
                            .where(
                                AuditEvent.task_id == task_id,
                                AuditEvent.sandbox_id == principal.sandbox_id,
                                AuditEvent.seq > last_seq,
                            )
                            .order_by(AuditEvent.seq)
                        )
                    )
                    state = poll.scalar(
                        select(Task.state).where(
                            Task.id == task_id,
                            Task.sandbox_id == principal.sandbox_id,
                            Task.user_id == principal.user_id,
                        )
                    )
                for event in events:
                    last_seq = event.seq
                    payload = {
                        "seq": event.seq,
                        "type": event.event_type,
                        "payload": event.payload_json,
                        "task_state": state,
                    }
                    yield f"id: {event.seq}\nevent: task-event\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if state in {"SUCCEEDED", "FAILED", "CLOSED"} and not events:
                    return
                time.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    install_runtime_routes(application, get_session, get_banking_principal)
    install_report_routes(application, get_session, get_banking_principal)
    install_extension_routes(application, get_session, get_banking_principal)
    install_wealth_routes(application, get_session, get_banking_principal)
    from .asset_api import install_asset_routes
    from .customer_profile_api import install_customer_profile_routes
    install_asset_routes(application, get_session, get_banking_principal)
    install_customer_profile_routes(application, session_factory, get_banking_principal)
    from .credit_api import install_credit_routes
    from .cards_api import install_card_routes
    install_credit_routes(application, session_factory, get_principal)
    install_card_routes(application, session_factory, get_banking_principal)
    install_personal_extension_routes(application, get_session, get_banking_principal)
    from .accounts import install_account_routes
    install_account_routes(application, get_session, get_principal)
    from .routing_api import install_routing_routes
    install_routing_routes(application, get_session, get_principal)
    from .admin_api import install_admin_routes
    from .test_data_api import install_test_data_routes
    install_admin_routes(application, get_session, get_principal)
    install_test_data_routes(application, get_session, get_principal)
    from .internal_banking_api import install_internal_banking_routes
    install_internal_banking_routes(application, get_session, get_banking_principal)
    from .fx_api import install_fx_routes
    install_fx_routes(application, get_session, get_banking_principal)
    from .contact_api import install_contact_routes
    install_contact_routes(application, get_session, get_banking_principal)
    from .decision_api import install_decision_routes
    install_decision_routes(application, get_session, get_principal)
    from .conversation_api import install_conversation_routes
    install_conversation_routes(application, get_session, get_principal)
    from .attachment_api import install_attachment_routes
    install_attachment_routes(application, get_session, get_principal)
    from .attachment_admin_api import install_attachment_admin_routes
    install_attachment_admin_routes(application, get_session, get_principal)
    from .presentation_api import install_presentation_routes
    install_presentation_routes(application, get_session, get_principal)
    from .knowledge_admin_api import install_knowledge_routes
    install_knowledge_routes(application, get_session, get_principal)
    return application


app = create_app()
