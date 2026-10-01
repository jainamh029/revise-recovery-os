"""SQLAlchemy 2.0 data model (see TRD section 4).

Money is Decimal end-to-end. Percentages are stored as decimals (0.25, never 25).
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(14, 2, asdecimal=True)
PCT = Numeric(7, 4, asdecimal=True)
HOURS = Numeric(10, 2, asdecimal=True)


def _id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_now = utcnow


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Partner(Base, Timestamped):
    __tablename__ = "partners"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(255))
    partner_type: Mapped[str] = mapped_column(String(50))
    geography: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    expected_monthly_volume: Mapped[int | None] = mapped_column(Integer)
    data_security_requirement: Mapped[str] = mapped_column(String(50), default="standard")
    sourcing_method: Mapped[str | None] = mapped_column(String(100))
    logistics_method: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="active")
    notes: Mapped[str | None] = mapped_column(Text)


class PartnerContract(Base, Timestamped):
    __tablename__ = "partner_contracts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"))
    contract_name: Mapped[str] = mapped_column(String(255))
    contract_type: Mapped[str] = mapped_column(String(50))
    inventory_ownership_model: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    acquisition_cost_per_unit = mapped_column(MONEY)
    service_fee_per_unit = mapped_column(MONEY)
    revenue_share_pct = mapped_column(PCT)
    expected_monthly_volume: Mapped[int | None] = mapped_column(Integer)
    minimum_volume_commitment: Mapped[int | None] = mapped_column(Integer)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
    document_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    partner: Mapped[Partner] = relationship()


class DeviceModelProfile(Base, Timestamped):
    __tablename__ = "device_model_profiles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str] = mapped_column(String(255))
    device_category: Mapped[str] = mapped_column(String(50), default="laptop")
    typical_age_years = mapped_column(Numeric(5, 2, asdecimal=True))
    expected_process_minutes = mapped_column(Numeric(10, 2, asdecimal=True))
    expected_first_pass_yield_pct = mapped_column(PCT)
    expected_exception_rate_pct = mapped_column(PCT)
    expected_repair_cost = mapped_column(MONEY)
    expected_resale_price = mapped_column(MONEY)
    expected_sell_through_pct = mapped_column(PCT)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DeviceCohort(Base, Timestamped):
    __tablename__ = "device_cohorts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"))
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("partner_contracts.id"))
    cohort_code: Mapped[str] = mapped_column(String(100), unique=True)
    cohort_name: Mapped[str] = mapped_column(String(255))
    intake_date: Mapped[date | None] = mapped_column(Date)
    expected_device_count: Mapped[int] = mapped_column(Integer)
    actual_device_count: Mapped[int] = mapped_column(Integer, default=0)
    ownership_model: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    priority_score = mapped_column(Numeric(14, 4, asdecimal=True))
    target_completion_date: Mapped[date | None] = mapped_column(Date)
    target_sale_date: Mapped[date | None] = mapped_column(Date)
    approved_budget_version_id: Mapped[str | None] = mapped_column(String(36))
    margin_floor_pct = mapped_column(PCT, default=0)
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    partner: Mapped[Partner] = relationship()
    contract: Mapped[PartnerContract | None] = relationship()


class CohortBudgetVersion(Base):
    __tablename__ = "cohort_budget_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    scenario_type: Mapped[str] = mapped_column(String(30))  # base|downside|upside|reforecast
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    expected_units_received: Mapped[int] = mapped_column(Integer)
    expected_units_processed: Mapped[int] = mapped_column(Integer)
    expected_units_sold: Mapped[int] = mapped_column(Integer)
    expected_acquisition_cost = mapped_column(MONEY)
    expected_inbound_logistics_cost = mapped_column(MONEY)
    expected_robot_hours = mapped_column(MONEY)
    expected_robot_cost = mapped_column(MONEY)
    expected_manual_labor_cost = mapped_column(MONEY)
    expected_repair_cost = mapped_column(MONEY)
    expected_outbound_fulfillment_cost = mapped_column(MONEY)
    expected_marketplace_fees = mapped_column(MONEY)
    expected_revenue = mapped_column(MONEY)
    expected_contribution_margin = mapped_column(MONEY)
    expected_cash_exposure = mapped_column(MONEY)
    expected_collection_days: Mapped[int | None] = mapped_column(Integer)
    assumptions: Mapped[dict | None] = mapped_column(JSON)  # full input set, for audit + reforecast
    economics: Mapped[dict | None] = mapped_column(JSON)  # full computed output snapshot
    recommendation: Mapped[dict | None] = mapped_column(JSON)
    decision_rationale: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CohortModelMix(Base):
    __tablename__ = "cohort_model_mix"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    device_model_profile_id: Mapped[str] = mapped_column(ForeignKey("device_model_profiles.id"))
    expected_unit_count: Mapped[int] = mapped_column(Integer)
    expected_condition_grade: Mapped[str | None] = mapped_column(String(20))
    expected_resale_price = mapped_column(MONEY)
    expected_process_minutes = mapped_column(Numeric(10, 2, asdecimal=True))
    expected_exception_rate_pct = mapped_column(PCT)
    expected_repair_cost = mapped_column(MONEY)
    profile: Mapped[DeviceModelProfile] = relationship()


class RobotCell(Base, Timestamped):
    __tablename__ = "robot_cells"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cell_name: Mapped[str] = mapped_column(String(100), unique=True)
    location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="available")
    standard_daily_capacity_units: Mapped[int | None] = mapped_column(Integer)
    standard_daily_capacity_hours = mapped_column(HOURS)
    supported_device_categories: Mapped[list | None] = mapped_column(JSON)
    supported_workflows: Mapped[list | None] = mapped_column(JSON)


class CapacityCalendar(Base):
    __tablename__ = "capacity_calendar"
    __table_args__ = (UniqueConstraint("robot_cell_id", "calendar_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    robot_cell_id: Mapped[str] = mapped_column(ForeignKey("robot_cells.id"))
    calendar_date: Mapped[date] = mapped_column(Date)
    scheduled_hours = mapped_column(HOURS)
    maintenance_hours = mapped_column(HOURS, default=0)
    downtime_hours = mapped_column(HOURS, default=0)
    assigned_hours = mapped_column(HOURS, default=0)
    available_hours = mapped_column(HOURS)
    status: Mapped[str] = mapped_column(String(30), default="open")


class CohortAssignment(Base, Timestamped):
    __tablename__ = "cohort_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    robot_cell_id: Mapped[str] = mapped_column(ForeignKey("robot_cells.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    planned_hours = mapped_column(HOURS)
    planned_units: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="planned")
    daily_plan: Mapped[dict | None] = mapped_column(JSON)  # {iso_date: hours}
    robot_cell: Mapped[RobotCell] = relationship()


class DailyOperation(Base, Timestamped):
    __tablename__ = "daily_operations"
    __table_args__ = (UniqueConstraint("cohort_id", "robot_cell_id", "operation_date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    robot_cell_id: Mapped[str] = mapped_column(ForeignKey("robot_cells.id"))
    operation_date: Mapped[date] = mapped_column(Date)
    devices_received: Mapped[int] = mapped_column(Integer, default=0)
    devices_started: Mapped[int] = mapped_column(Integer, default=0)
    devices_completed: Mapped[int] = mapped_column(Integer, default=0)
    first_pass_completions: Mapped[int] = mapped_column(Integer, default=0)
    manual_exception_count: Mapped[int] = mapped_column(Integer, default=0)
    devices_under_repair: Mapped[int] = mapped_column(Integer, default=0)
    quality_approved_count: Mapped[int] = mapped_column(Integer, default=0)
    rework_events: Mapped[int] = mapped_column(Integer, default=0)
    productive_robot_hours = mapped_column(HOURS, default=0)
    downtime_hours = mapped_column(HOURS, default=0)
    downtime_reason: Mapped[str | None] = mapped_column(String(255))
    technician_hours = mapped_column(HOURS, default=0)
    labor_cost = mapped_column(MONEY, default=0)
    repair_parts_cost = mapped_column(MONEY, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class ExceptionEvent(Base):
    __tablename__ = "exception_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    device_id: Mapped[str | None] = mapped_column(String(36))
    device_model_profile_id: Mapped[str | None] = mapped_column(ForeignKey("device_model_profiles.id"))
    robot_cell_id: Mapped[str | None] = mapped_column(ForeignKey("robot_cells.id"))
    exception_type: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    status: Mapped[str] = mapped_column(String(30), default="open")
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=True)
    estimated_resolution_cost = mapped_column(MONEY)
    actual_resolution_cost = mapped_column(MONEY)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolution_notes: Mapped[str | None] = mapped_column(Text)


class CostEntry(Base):
    __tablename__ = "cost_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str | None] = mapped_column(ForeignKey("device_cohorts.id"))
    partner_id: Mapped[str | None] = mapped_column(ForeignKey("partners.id"))
    robot_cell_id: Mapped[str | None] = mapped_column(ForeignKey("robot_cells.id"))
    cost_date: Mapped[date] = mapped_column(Date)
    cost_category: Mapped[str] = mapped_column(String(50))
    amount = mapped_column(MONEY)
    quantity = mapped_column(Numeric(14, 4, asdecimal=True))
    unit_cost = mapped_column(Numeric(14, 4, asdecimal=True))
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str | None] = mapped_column(String(50))
    source_ref: Mapped[str | None] = mapped_column(String(100))  # e.g. daily_ops:<id> for idempotent re-post
    allocation_rule: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ResaleListing(Base, Timestamped):
    __tablename__ = "resale_listings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    listing_channel: Mapped[str] = mapped_column(String(100))
    listing_date: Mapped[date | None] = mapped_column(Date)
    listing_price = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    device_count: Mapped[int] = mapped_column(Integer, default=1)


class SalesTransaction(Base, Timestamped):
    __tablename__ = "sales_transactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"))
    resale_listing_id: Mapped[str | None] = mapped_column(ForeignKey("resale_listings.id"))
    sale_date: Mapped[date | None] = mapped_column(Date)
    units_sold: Mapped[int] = mapped_column(Integer, default=1)
    units_returned: Mapped[int] = mapped_column(Integer, default=0)
    gross_sale_amount = mapped_column(MONEY)
    marketplace_fee = mapped_column(MONEY, default=0)
    shipping_cost = mapped_column(MONEY, default=0)
    return_cost = mapped_column(MONEY, default=0)
    refund_amount = mapped_column(MONEY, default=0)
    discount_amount = mapped_column(MONEY, default=0)
    net_sale_amount = mapped_column(MONEY)
    payment_status: Mapped[str] = mapped_column(String(30), default="pending")
    payout_due_date: Mapped[date | None] = mapped_column(Date)
    payout_date: Mapped[date | None] = mapped_column(Date)


class Invoice(Base, Timestamped):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str | None] = mapped_column(ForeignKey("device_cohorts.id"))
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"))
    invoice_number: Mapped[str] = mapped_column(String(100), unique=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    amount = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(30), default="sent")
    partner: Mapped[Partner] = relationship()


class CashReceipt(Base):
    __tablename__ = "cash_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("invoices.id"))
    sales_transaction_id: Mapped[str | None] = mapped_column(ForeignKey("sales_transactions.id"))
    receipt_date: Mapped[date] = mapped_column(Date)
    amount = mapped_column(MONEY)
    receipt_type: Mapped[str] = mapped_column(String(50))  # invoice_payment|marketplace_payout|adjustment
    reference_number: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    alert_type: Mapped[str] = mapped_column(String(100))
    severity: Mapped[str] = mapped_column(String(20))  # critical|warning|info
    entity_type: Mapped[str] = mapped_column(String(50))  # cohort|partner|robot_cell|device_model|invoice
    entity_id: Mapped[str] = mapped_column(String(36))
    cohort_id: Mapped[str | None] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open")  # open|resolved|snoozed|escalated
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    comments: Mapped[list | None] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str | None] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[dict | None] = mapped_column(JSON)
    new_value: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PolicySetting(Base):
    """Single-row, editable underwriting policy (configurable thresholds)."""

    __tablename__ = "policy_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class Device(Base):
    """Serial-level traceability record. One row per physical laptop."""

    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cohort_id: Mapped[str] = mapped_column(ForeignKey("device_cohorts.id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True)
    asset_tag: Mapped[str | None] = mapped_column(String(100))
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(255))
    storage_type: Mapped[str] = mapped_column(String(30), default="ssd")  # ssd|hdd|nvme|emmc|none
    intake_condition_grade: Mapped[str | None] = mapped_column(String(5))
    intake_timestamp: Mapped[datetime] = mapped_column(DateTime, default=_now)
    current_location: Mapped[str | None] = mapped_column(String(100))
    current_custodian: Mapped[str | None] = mapped_column(String(100))
    current_status: Mapped[str] = mapped_column(String(30), default="RECEIVED", index=True)
    data_sanitization_required: Mapped[bool] = mapped_column(Boolean, default=True)
    sanitization_method: Mapped[str | None] = mapped_column(String(100))
    sanitization_status: Mapped[str] = mapped_column(
        String(20), default="PENDING"
    )  # PENDING|SANITIZED|FAILED|NOT_REQUIRED
    sanitization_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    sanitization_certificate_id: Mapped[str | None] = mapped_column(String(100))
    quality_control_status: Mapped[str] = mapped_column(
        String(20), default="PENDING"
    )  # PENDING|PASSED|FAILED
    final_disposition: Mapped[str] = mapped_column(
        String(20), default="PENDING"
    )  # PENDING|RESALE|RECYCLE|DESTROY
    disposition_reason_code: Mapped[str | None] = mapped_column(String(50))
    disposition_operator: Mapped[str | None] = mapped_column(String(100))
    disposition_at: Mapped[datetime | None] = mapped_column(DateTime)
    resale_listing_id: Mapped[str | None] = mapped_column(ForeignKey("resale_listings.id"))
    sales_transaction_id: Mapped[str | None] = mapped_column(ForeignKey("sales_transactions.id"))


class DeviceCustodyEvent(Base):
    """Append-only chain-of-custody ledger. UPDATE and DELETE are rejected (see listeners below)."""

    __tablename__ = "device_custody_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    event_type: Mapped[str] = mapped_column(
        String(40)
    )  # received|transfer|status_change|sanitization|quality_control|disposition|listing|sale|return
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str | None] = mapped_column(String(30))
    location: Mapped[str | None] = mapped_column(String(100))
    custodian: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


from sqlalchemy import event  # noqa: E402


@event.listens_for(DeviceCustodyEvent, "before_update")
def _no_update(*_):
    raise ValueError("Custody events are immutable.")


@event.listens_for(DeviceCustodyEvent, "before_delete")
def _no_delete(*_):
    raise ValueError("Custody events are immutable.")
