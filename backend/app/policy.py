"""Configurable underwriting policy. Defaults are illustrative, not Revise's real thresholds."""

from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import PolicySetting


@dataclass(frozen=True)
class Policy:
    # Illustrative synthetic thresholds. Every CM% below is contribution margin / NET RECOGNIZED REVENUE.
    base_case_accept_cm_pct_net_revenue: Decimal = Decimal("0.30")
    base_case_review_cm_pct_net_revenue: Decimal = Decimal("0.15")  # below this => decline
    downside_case_min_cm_pct_net_revenue: Decimal = Decimal("0.15")
    live_margin_floor_cm_pct_net_revenue: Decimal = Decimal("0.15")
    critical_negative_margin_threshold: Decimal = Decimal(
        "0.00"
    )  # forecast CM dollars below this => stop / loss-making
    max_exception_rate: Decimal = Decimal("0.20")
    min_first_pass_yield: Decimal = Decimal("0.80")
    min_sell_through: Decimal = Decimal("0.75")
    target_days_to_sale: int = 45
    target_collection_days: int = 30
    utilization_warning: Decimal = Decimal("0.65")
    throughput_miss_warning: Decimal = Decimal("0.15")
    cost_overrun_warning: Decimal = Decimal("0.10")
    exception_alert_multiple: Decimal = Decimal("1.25")
    min_quality_index: Decimal = Decimal("0.45")
    working_capital_limit: Decimal = Decimal("180000")
    review_capacity_per_day: int = 40  # manual exception reviews the tech team can clear per day
    supported_wipe_levels: tuple = ("standard", "enhanced")
    robot_cost_per_hour: Decimal = Decimal("42")
    labor_rate_per_hour: Decimal = Decimal("32")
    manual_minutes_per_exception: Decimal = Decimal("35")
    # priority score weights (sum to 1)
    w_cm_per_hour: Decimal = Decimal("0.50")
    w_cm_pct: Decimal = Decimal("0.20")
    w_low_exception: Decimal = Decimal("0.15")
    w_cash_efficiency: Decimal = Decimal("0.15")


_TUPLE_FIELDS = {"supported_wipe_levels"}
_INT_FIELDS = {"target_days_to_sale", "target_collection_days", "review_capacity_per_day"}


def _coerce(name: str, value):
    if name in _TUPLE_FIELDS:
        return tuple(value)
    if name in _INT_FIELDS:
        return int(value)
    return Decimal(str(value))


def get_policy(db: Session | None) -> Policy:
    defaults = Policy()
    if db is None:
        return defaults
    row = db.get(PolicySetting, 1)
    if not row or not row.values:
        return defaults
    merged = {k: _coerce(k, v) for k, v in row.values.items() if hasattr(defaults, k)}
    return Policy(**{**asdict(defaults), **merged})


def policy_to_json(p: Policy) -> dict:
    return {
        k: (list(v) if isinstance(v, tuple) else (float(v) if isinstance(v, Decimal) else v))
        for k, v in asdict(p).items()
    }


def update_policy(db: Session, patch: dict) -> Policy:
    row = db.get(PolicySetting, 1) or PolicySetting(id=1, values={})
    cur = dict(row.values or {})
    for k, v in patch.items():
        if not hasattr(Policy, k) and k not in Policy.__dataclass_fields__:
            raise ValueError(f"Unknown policy field: {k}")
        _coerce(k, v)
        cur[k] = v
    row.values = cur
    db.merge(row)
    db.commit()
    return get_policy(db)
