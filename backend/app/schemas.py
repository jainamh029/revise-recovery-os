"""Request schemas. Inputs only -- responses are plain dicts from the service layer."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Pct = Field(ge=0, le=1, description="Decimal fraction, e.g. 0.25 for 25%")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PartnerIn(Strict):
    name: str
    partner_type: str
    geography: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    payment_terms_days: int = Field(30, ge=0)
    expected_monthly_volume: int | None = Field(None, ge=0)
    data_security_requirement: str = "standard"
    sourcing_method: str | None = None
    logistics_method: str | None = None
    status: str = "active"
    notes: str | None = None


PARTNER_TYPES = {
    "enterprise_device_supplier",
    "itad_provider",
    "recycler",
    "leasing_provider",
    "nonprofit",
    "resale_channel",
    "processing_customer",
    "other",
    "university",
}
CONTRACT_TYPES = {"supply_purchase", "processing_service", "revenue_share", "hybrid"}
OWNERSHIP = {"partner_owned", "operator_owned", "shared", "unknown"}


class ContractIn(Strict):
    partner_id: str
    contract_name: str
    contract_type: str
    inventory_ownership_model: str
    start_date: date | None = None
    end_date: date | None = None
    acquisition_cost_per_unit: Decimal | None = Field(None, ge=0)
    service_fee_per_unit: Decimal | None = Field(None, ge=0)
    revenue_share_pct: Decimal | None = Field(None, ge=0, le=1)
    expected_monthly_volume: int | None = Field(None, ge=0)
    minimum_volume_commitment: int | None = Field(None, ge=0)
    payment_terms_days: int | None = Field(None, ge=0)
    document_ref: str | None = None
    status: str = "draft"

    @model_validator(mode="after")
    def _check(self):
        if self.contract_type not in CONTRACT_TYPES:
            raise ValueError(f"contract_type must be one of {sorted(CONTRACT_TYPES)}")
        if self.inventory_ownership_model not in OWNERSHIP:
            raise ValueError(f"inventory_ownership_model must be one of {sorted(OWNERSHIP)}")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("Contract end date cannot precede its start date.")
        return self


class LineIn(Strict):
    model_name: str
    device_model_profile_id: str | None = None
    units: int = Field(gt=0)
    condition_grade: str = "B"
    process_minutes: Decimal = Field(gt=0)
    exception_rate: Decimal = Pct
    first_pass_yield: Decimal = Pct
    repair_cost: Decimal = Field(ge=0)
    resale_price: Decimal = Field(ge=0)
    sell_through: Decimal = Pct


class AssumptionsIn(Strict):
    lines: list[LineIn] = Field(min_length=1)
    contract_type: str | None = None
    ownership: str | None = None
    acquisition_cost_per_unit: Decimal | None = Field(None, ge=0)
    inbound_logistics_per_unit: Decimal | None = Field(None, ge=0)
    outbound_shipping_per_unit: Decimal | None = Field(None, ge=0)
    marketplace_fee_rate: Decimal | None = Field(None, ge=0, le=1)
    return_rate: Decimal | None = Field(None, ge=0, le=1)
    discount_rate: Decimal | None = Field(None, ge=0, le=1)
    service_fee_per_unit: Decimal | None = Field(None, ge=0)
    revenue_share_pct: Decimal | None = Field(None, ge=0, le=1)
    performance_fee: Decimal | None = Field(None, ge=0)
    repair_recovery_rate: Decimal | None = Field(None, ge=0, le=1)
    robot_cost_per_hour: Decimal | None = Field(None, ge=0)
    labor_rate_per_hour: Decimal | None = Field(None, ge=0)
    manual_minutes_per_exception: Decimal | None = Field(None, ge=0)
    planned_uptime: Decimal | None = Field(None, gt=0, le=1)
    days_to_sale: int | None = Field(None, ge=0)
    payout_days: int | None = Field(None, ge=0)
    margin_floor_pct: Decimal | None = Field(None, ge=0, le=1)


class CohortIn(Strict):
    partner_id: str
    contract_id: str | None = None
    cohort_code: str | None = None
    cohort_name: str
    intake_date: date | None = None
    target_completion_date: date | None = None
    target_sale_date: date | None = None
    assumptions: AssumptionsIn


class CohortPatch(Strict):
    cohort_name: str | None = None
    intake_date: date | None = None
    target_completion_date: date | None = None
    target_sale_date: date | None = None


class PreviewIn(Strict):
    partner_id: str
    contract_id: str | None = None
    intake_date: date | None = None
    target_completion_date: date | None = None
    assumptions: AssumptionsIn


class DecisionIn(Strict):
    approver: str = "demo.approver"
    rationale: str
    override: bool = False


class ScenarioIn(Strict):
    price_mult: Decimal | None = Field(None, gt=0)
    sell_through_mult: Decimal | None = Field(None, gt=0)
    exception_mult: Decimal | None = Field(None, ge=0)
    process_mult: Decimal | None = Field(None, gt=0)
    repair_mult: Decimal | None = Field(None, ge=0)
    volume_mult: Decimal | None = Field(None, gt=0)
    uptime: Decimal | None = Field(None, gt=0, le=1)
    return_rate: Decimal | None = Field(None, ge=0, le=1)


class CalendarIn(Strict):
    robot_cell_id: str
    start_date: date
    end_date: date
    scheduled_hours: Decimal = Field(Decimal("10"), ge=0, le=24)
    maintenance_hours: Decimal = Field(Decimal("0"), ge=0)
    weekdays_only: bool = True


class AssignmentIn(Strict):
    robot_cell_id: str
    start_date: date
    end_date: date
    planned_hours: Decimal | None = Field(None, gt=0)
    planned_units: int | None = Field(None, gt=0)


class DailyOpIn(Strict):
    cohort_id: str
    robot_cell_id: str
    operation_date: date
    devices_received: int = Field(0, ge=0)
    devices_started: int = Field(0, ge=0)
    devices_completed: int = Field(0, ge=0)
    first_pass_completions: int = Field(0, ge=0)
    manual_exception_count: int = Field(0, ge=0)
    devices_under_repair: int = Field(0, ge=0)
    quality_approved_count: int = Field(0, ge=0)
    rework_events: int = Field(0, ge=0)
    productive_robot_hours: Decimal = Field(Decimal("0"), ge=0)
    downtime_hours: Decimal = Field(Decimal("0"), ge=0)
    downtime_reason: str | None = None
    technician_hours: Decimal = Field(Decimal("0"), ge=0)
    labor_cost: Decimal = Field(Decimal("0"), ge=0)
    repair_parts_cost: Decimal = Field(Decimal("0"), ge=0)
    notes: str | None = None


class ExceptionIn(Strict):
    cohort_id: str
    device_model_profile_id: str | None = None
    robot_cell_id: str | None = None
    exception_type: str
    severity: str = "warning"
    requires_manual_review: bool = True
    estimated_resolution_cost: Decimal | None = Field(None, ge=0)


class ExceptionPatch(Strict):
    status: str | None = None
    actual_resolution_cost: Decimal | None = Field(None, ge=0)
    resolution_notes: str | None = None


class CostIn(Strict):
    cohort_id: str | None = None
    partner_id: str | None = None
    robot_cell_id: str | None = None
    cost_date: date
    cost_category: str
    amount: Decimal = Field(ge=0)
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    description: str | None = None
    allocation_rule: str | None = None
    source_type: str | None = "manual"


class ListingIn(Strict):
    cohort_id: str
    listing_channel: str
    listing_date: date
    listing_price: Decimal = Field(ge=0)
    device_count: int = Field(gt=0)


class SaleIn(Strict):
    cohort_id: str
    resale_listing_id: str | None = None
    sale_date: date
    units_sold: int = Field(1, gt=0)
    gross_sale_amount: Decimal = Field(ge=0)
    marketplace_fee: Decimal = Field(Decimal("0"), ge=0)
    shipping_cost: Decimal = Field(Decimal("0"), ge=0)
    return_cost: Decimal = Field(Decimal("0"), ge=0)
    refund_amount: Decimal = Field(Decimal("0"), ge=0)
    discount_amount: Decimal = Field(Decimal("0"), ge=0)
    units_returned: int = Field(0, ge=0)
    payout_due_date: date | None = None
    payout_days: int = 7


class ReturnIn(Strict):
    units: int = Field(gt=0)
    refund_amount: Decimal = Field(ge=0)
    return_cost: Decimal = Field(Decimal("0"), ge=0)


class InvoiceIn(Strict):
    cohort_id: str | None = None
    partner_id: str
    invoice_number: str
    invoice_date: date
    due_date: date
    amount: Decimal = Field(gt=0)


class ReceiptIn(Strict):
    invoice_id: str | None = None
    sales_transaction_id: str | None = None
    receipt_date: date
    amount: Decimal
    receipt_type: str | None = None
    reference_number: str | None = None


class AlertActionIn(Strict):
    action: str
    text: str | None = None
    owner: str | None = None
    snooze_days: int = 3
    user: str = "demo.user"
