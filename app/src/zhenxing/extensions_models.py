from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class FraudLabel(Base):
    __tablename__ = "extension_fraud_labels"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "payee_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    payee_id: Mapped[str] = mapped_column(ForeignKey("payees.id"))
    risk_level: Mapped[str] = mapped_column(String(20))
    reason_code: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(50), default="SYNTHETIC_BANK_RISK_FEED")
    version: Mapped[int] = mapped_column(Integer, default=1)


class FamilyMember(Base):
    __tablename__ = "extension_family_members"
    __table_args__ = (UniqueConstraint("owner_user_id", "helper_user_id"), UniqueConstraint("owner_user_id", "display_name"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    helper_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(40))


class FamilyDelegation(Base):
    __tablename__ = "extension_family_delegations"
    __table_args__ = (UniqueConstraint("owner_user_id", "grantee_user_id"), CheckConstraint("maximum_amount_minor >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    grantee_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    scopes_json: Mapped[list] = mapped_column(JSON)
    maximum_amount_minor: Mapped[int] = mapped_column(Integer)
    limit_period: Mapped[str] = mapped_column(String(16), default="PER_PROPOSAL")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)


class FamilyProposal(Base):
    __tablename__ = "extension_family_proposals"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    grantee_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    delegation_id: Mapped[str] = mapped_column(ForeignKey("extension_family_delegations.id"))
    proposal_kind: Mapped[str] = mapped_column(String(30))
    args_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_OWNER_APPROVAL")
    owner_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    operation_key: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    business_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quota_month: Mapped[str | None] = mapped_column(String(7), index=True, nullable=True)


class CashflowGoal(Base):
    __tablename__ = "extension_cashflow_goals"
    __table_args__ = (UniqueConstraint("user_id", "name"), CheckConstraint("target_minor > 0 AND reserved_minor >= 0 AND reserved_minor <= target_minor"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(60))
    target_minor: Mapped[int] = mapped_column(Integer)
    reserved_minor: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class GoalLedgerEntry(Base):
    __tablename__ = "extension_goal_ledger"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_key", "account_ref"), CheckConstraint("delta_minor <> 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    goal_id: Mapped[str] = mapped_column(ForeignKey("extension_cashflow_goals.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    account_ref: Mapped[str] = mapped_column(String(80))
    delta_minor: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CarbonPool(Base):
    __tablename__ = "extension_carbon_pools"
    __table_args__ = (CheckConstraint("remaining_points >= 0"),)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), primary_key=True)
    remaining_points: Mapped[int] = mapped_column(Integer, default=10000)


class CarbonWallet(Base):
    __tablename__ = "extension_carbon_wallets"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id"), CheckConstraint("points >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    points: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CarbonBehavior(Base):
    __tablename__ = "extension_carbon_behaviors"
    __table_args__ = (UniqueConstraint("user_id", "source_event_key"), UniqueConstraint("user_id", "name"), CheckConstraint("distance_m > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(20))
    distance_m: Mapped[int] = mapped_column(Integer)
    source_event_key: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(50), default="SYNTHETIC_BANK_FEED")
    status: Mapped[str] = mapped_column(String(30), default="VERIFIED_SYNTHETIC")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CarbonCredit(Base):
    __tablename__ = "extension_carbon_credits"
    __table_args__ = (UniqueConstraint("behavior_id"), UniqueConstraint("sandbox_id", "operation_key"), CheckConstraint("points > 0 AND estimated_avoided_grams >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    behavior_id: Mapped[str] = mapped_column(ForeignKey("extension_carbon_behaviors.id"))
    methodology_version: Mapped[str] = mapped_column(String(50))
    methodology_json: Mapped[dict] = mapped_column(JSON)
    estimated_avoided_grams: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    operation_key: Mapped[str] = mapped_column(String(120))


class CarbonPointLedger(Base):
    __tablename__ = "extension_carbon_point_ledger"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_key", "account_ref"), CheckConstraint("delta_points <> 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    account_ref: Mapped[str] = mapped_column(String(80))
    delta_points: Mapped[int] = mapped_column(Integer)


class CarbonReward(Base):
    __tablename__ = "extension_carbon_rewards"
    __table_args__ = (UniqueConstraint("sandbox_id", "name"), CheckConstraint("cost_points > 0 AND stock >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    cost_points: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CarbonRedemption(Base):
    __tablename__ = "extension_carbon_redemptions"
    __table_args__ = (UniqueConstraint("sandbox_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reward_id: Mapped[str] = mapped_column(ForeignKey("extension_carbon_rewards.id"))
    cost_points: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="ISSUED_SIMULATION")
    operation_key: Mapped[str] = mapped_column(String(120))


EXTENSION_TABLES = {
    table.__tablename__ for table in (
        FraudLabel, FamilyMember, FamilyDelegation, FamilyProposal, CashflowGoal, GoalLedgerEntry,
        CarbonPool, CarbonWallet, CarbonBehavior, CarbonCredit, CarbonPointLedger, CarbonReward, CarbonRedemption,
    )
}
