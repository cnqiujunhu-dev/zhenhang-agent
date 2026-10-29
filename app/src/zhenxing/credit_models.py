"""Additive deposit bindings and a separate dual-currency credit liability book.

Existing cards remain deposit cards with their non-null Account foreign key.
Credit limits are never Account.balance_minor, cash, or an investment asset.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, event, inspect
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import new_id, utcnow


class CreditAccount(Base):
    __tablename__ = "credit_accounts"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "facility_key"),
        CheckConstraint("base_credit_limit_minor>=0 AND posted_exposure_minor>=0 AND authorization_exposure_minor>=0 AND base_credit_limit_minor>=posted_exposure_minor+authorization_exposure_minor"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    facility_key: Mapped[str] = mapped_column(String(80), default="PRIMARY")
    base_currency: Mapped[str] = mapped_column(String(3), default="CNY")
    base_credit_limit_minor: Mapped[int] = mapped_column(BigInteger)
    posted_exposure_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    authorization_exposure_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditCurrencyBalance(Base):
    __tablename__ = "credit_currency_balances"
    __table_args__ = (UniqueConstraint("credit_account_id", "currency"),
        CheckConstraint("currency IN ('CNY','USD') AND principal_minor>=0 AND interest_minor>=0 AND fees_minor>=0 AND credit_balance_minor>=0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    principal_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    interest_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    fees_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CreditCard(Base):
    __tablename__ = "credit_cards"
    __table_args__ = (UniqueConstraint("card_number"), CheckConstraint("transaction_limit_minor>0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    card_number: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="ISSUED")
    transaction_limit_minor: Mapped[int] = mapped_column(BigInteger, default=2_000_000)
    version: Mapped[int] = mapped_column(Integer, default=1)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentInstrumentBinding(Base):
    __tablename__ = "payment_instrument_bindings"
    __table_args__ = (UniqueConstraint("card_number"), UniqueConstraint("instrument_type", "card_id"),
        CheckConstraint("(instrument_type='DEBIT' AND account_id IS NOT NULL AND credit_account_id IS NULL) OR (instrument_type='CREDIT' AND account_id IS NULL AND credit_account_id IS NOT NULL)"))
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_type: Mapped[str] = mapped_column(String(12))
    card_id: Mapped[str] = mapped_column(String(36))
    card_number: Mapped[str] = mapped_column(String(32))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    credit_account_id: Mapped[str | None] = mapped_column(ForeignKey("credit_accounts.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CustomerPaymentDefaults(Base):
    __tablename__ = "customer_payment_defaults"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_instrument_id: Mapped[str] = mapped_column(String(64))
    recipient_instrument_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class PaymentUpgradeRecord(Base):
    __tablename__ = "payment_upgrade_records"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    revision: Mapped[str] = mapped_column(String(60))
    before_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CardAccountBindingHistory(Base):
    __tablename__ = "card_account_binding_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    card_id: Mapped[str] = mapped_column(ForeignKey("cards.id"), index=True)
    previous_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    current_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    migration_id: Mapped[str] = mapped_column(ForeignKey("payment_upgrade_records.id"))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentCashReservation(Base):
    __tablename__ = "payment_cash_reservations"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "reservation_key"),
        CheckConstraint("amount_minor>0 AND remaining_minor>=0 AND remaining_minor<=amount_minor"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    reservation_key: Mapped[str] = mapped_column(String(120))
    owner_ref: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentJournal(Base):
    __tablename__ = "payment_journals"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentLedgerEntry(Base):
    __tablename__ = "payment_ledger_entries"
    __table_args__ = (UniqueConstraint("journal_id", "currency", "account_ref"), CheckConstraint("delta_minor<>0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    journal_id: Mapped[str] = mapped_column(ForeignKey("payment_journals.id"), index=True)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_ref: Mapped[str] = mapped_column(String(160))
    currency: Mapped[str] = mapped_column(String(3))
    delta_minor: Mapped[int] = mapped_column(BigInteger)


class PaymentRecord(Base):
    __tablename__ = "instrument_payments"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    instrument_id: Mapped[str] = mapped_column(String(64))
    instrument_type: Mapped[str] = mapped_column(String(12))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    credit_account_id: Mapped[str | None] = mapped_column(ForeignKey("credit_accounts.id"), nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(ForeignKey("payment_cash_reservations.id"), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    merchant_ref: Mapped[str] = mapped_column(String(120))
    authorization_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    journal_id: Mapped[str] = mapped_column(ForeignKey("payment_journals.id"))
    status: Mapped[str] = mapped_column(String(24), default="POSTED")
    receipt_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentRefund(Base):
    __tablename__ = "instrument_payment_refunds"
    __table_args__ = (UniqueConstraint("payment_id"), UniqueConstraint("sandbox_id", "user_id", "operation_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    payment_id: Mapped[str] = mapped_column(ForeignKey("instrument_payments.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    receipt_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditFxQuote(Base):
    __tablename__ = "credit_fx_quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    base_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    source_json: Mapped[dict] = mapped_column(JSON)
    scope_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditAuthorization(Base):
    __tablename__ = "credit_authorizations"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"), CheckConstraint("amount_minor>0 AND remaining_minor>=0 AND remaining_exposure_minor>=0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("credit_cards.id"))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    original_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    merchant_ref: Mapped[str] = mapped_column(String(120))
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    fx_quote_id: Mapped[str | None] = mapped_column(ForeignKey("credit_fx_quotes.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="AUTHORIZED")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditObligation(Base):
    __tablename__ = "credit_obligations"
    __table_args__ = (UniqueConstraint("payment_id"), CheckConstraint("remaining_minor>=0 AND remaining_exposure_minor>=0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("instrument_payments.id"))
    currency: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(20), default="PRINCIPAL")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    statement_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    installment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditOperation(Base):
    __tablename__ = "credit_operations"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditStatement(Base):
    __tablename__ = "credit_statements"
    __table_args__ = (UniqueConstraint("credit_account_id", "currency", "cycle_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    cycle_key: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditRepayment(Base):
    __tablename__ = "credit_repayments"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    receipt_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditRepaymentAllocation(Base):
    __tablename__ = "credit_repayment_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repayment_id: Mapped[str] = mapped_column(ForeignKey("credit_repayments.id"), index=True)
    obligation_id: Mapped[str] = mapped_column(ForeignKey("credit_obligations.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    base_exposure_minor: Mapped[int] = mapped_column(BigInteger)


class CreditInstallment(Base):
    __tablename__ = "credit_installments"
    __table_args__ = (UniqueConstraint("obligation_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"))
    obligation_id: Mapped[str] = mapped_column(ForeignKey("credit_obligations.id"))
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    periods: Mapped[int] = mapped_column(Integer)
    fee_minor: Mapped[int] = mapped_column(BigInteger)
    schedule_json: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE")
    policy_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditFeeCharge(Base):
    __tablename__ = "credit_fee_charges"
    __table_args__ = (UniqueConstraint("installment_id"), CheckConstraint("amount_minor>0 AND remaining_minor>=0 AND remaining_exposure_minor>=0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    credit_account_id: Mapped[str] = mapped_column(ForeignKey("credit_accounts.id"), index=True)
    installment_id: Mapped[str] = mapped_column(ForeignKey("credit_installments.id"), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_minor: Mapped[int] = mapped_column(BigInteger)
    base_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    remaining_exposure_minor: Mapped[int] = mapped_column(BigInteger)
    journal_id: Mapped[str] = mapped_column(ForeignKey("payment_journals.id"))
    policy_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditFeeAllocation(Base):
    __tablename__ = "credit_fee_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    repayment_id: Mapped[str] = mapped_column(ForeignKey("credit_repayments.id"), index=True)
    fee_charge_id: Mapped[str] = mapped_column(ForeignKey("credit_fee_charges.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    base_exposure_minor: Mapped[int] = mapped_column(BigInteger)


class CreditApplication(Base):
    __tablename__ = "credit_applications"
    __table_args__ = (UniqueConstraint("sandbox_id", "user_id", "operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sandbox_id: Mapped[str] = mapped_column(ForeignKey("sandboxes.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    credit_account_id: Mapped[str | None] = mapped_column(ForeignKey("credit_accounts.id"), nullable=True)
    requested_limit_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operation_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING_REVIEW")
    decision_json: Mapped[dict] = mapped_column(JSON, default=dict)
    card_id: Mapped[str | None] = mapped_column(ForeignKey("credit_cards.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


CREDIT_TABLES = {model.__tablename__ for model in (
    CreditAccount, CreditCurrencyBalance, CreditCard, PaymentInstrumentBinding, CustomerPaymentDefaults,
    PaymentUpgradeRecord, CardAccountBindingHistory, PaymentCashReservation, PaymentJournal, PaymentLedgerEntry,
    PaymentRecord, PaymentRefund, CreditFxQuote, CreditAuthorization, CreditObligation, CreditOperation,
    CreditStatement, CreditRepayment, CreditRepaymentAllocation, CreditInstallment, CreditApplication,
    CreditFeeCharge, CreditFeeAllocation)}


def _protect_fields(fields):
    def validate(_mapper, _connection, target):
        if any(inspect(target).attrs[name].history.has_changes() for name in fields):
            from .contracts import DomainError
            raise DomainError("CREDIT_POSTING_IMMUTABLE", "已记账经济事实与已出账快照不能改写。", 409)
    return validate


event.listen(PaymentRecord, "before_update", _protect_fields(("sandbox_id", "user_id", "instrument_id", "instrument_type",
    "account_id", "credit_account_id", "reservation_id", "amount_minor", "currency", "merchant_ref", "authorization_ref",
    "operation_key", "request_hash", "journal_id", "receipt_json", "created_at")))
event.listen(PaymentRefund, "before_update", _protect_fields(("sandbox_id", "user_id", "payment_id", "operation_key",
    "request_hash", "receipt_json", "created_at")))
event.listen(CreditStatement, "before_update", _protect_fields(("credit_account_id", "currency", "cycle_key", "period_start",
    "period_end", "due_at", "snapshot_json", "snapshot_hash", "created_at")))
event.listen(CreditFeeCharge, "before_update", _protect_fields(("credit_account_id", "installment_id", "currency",
    "amount_minor", "base_exposure_minor", "journal_id", "policy_json", "created_at")))
event.listen(CreditInstallment, "before_update", _protect_fields(("credit_account_id", "obligation_id", "currency",
    "operation_key", "request_hash", "principal_minor", "periods", "fee_minor", "schedule_json", "policy_hash", "created_at")))
event.listen(CreditApplication, "before_update", _protect_fields(("sandbox_id", "user_id", "kind", "requested_limit_minor",
    "operation_key", "request_hash", "created_at")))
event.listen(CreditCard, "before_update", _protect_fields(("sandbox_id", "user_id", "credit_account_id", "card_number", "application_id", "created_at")))
event.listen(CreditObligation, "before_update", _protect_fields(("credit_account_id", "payment_id", "currency",
    "amount_minor", "created_at")))
event.listen(CreditAuthorization, "before_update", _protect_fields(("sandbox_id", "user_id", "credit_account_id", "card_id",
    "currency", "amount_minor", "original_exposure_minor", "merchant_ref", "operation_key", "request_hash", "fx_quote_id",
    "expires_at", "created_at")))
