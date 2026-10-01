"""Exportable cohort underwriting memo (Markdown)."""

from __future__ import annotations

from ..config import DEMO_DISCLAIMER
from .underwriting import DECISION_LABEL


def _m(x) -> str:
    return f"${x:,.0f}"


def build(cohort, partner, contract, uw: dict, policy, approved: dict | None) -> str:
    s = uw["scenarios"]
    lines = [
        f"# Underwriting memo -- {cohort.cohort_code}: {cohort.cohort_name}",
        "",
        f"> {DEMO_DISCLAIMER}",
        "",
        f"**Partner:** {partner.name} ({partner.partner_type})  ",
        f"**Contract:** {contract.contract_name if contract else 'n/a'} "
        f"({contract.contract_type if contract else 'n/a'}, {contract.inventory_ownership_model if contract else 'n/a'})  ",
        f"**Devices:** {cohort.expected_device_count:,}  ",
        f"**Intake:** {cohort.intake_date}  ·  **Target completion:** {cohort.target_completion_date}  ",
        "",
        f"## Recommendation: {DECISION_LABEL[uw['decision']]}",
        "",
        *[f"- {r}" for r in uw["reasons"]],
        "",
        "## Economics by scenario",
        "",
        "| | Downside | Base | Upside |",
        "|---|---:|---:|---:|",
    ]

    def row(label, key, fmt):
        return f"| {label} | {fmt(s['downside'][key])} | {fmt(s['base'][key])} | {fmt(s['upside'][key])} |"

    pct = lambda v: "n/a" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    lines += [
        row("Gross sale proceeds", "gross_sale_proceeds", _m),
        row("Net recognized revenue", "net_recognized_revenue", _m),
        row("Direct variable cost", "direct_cost", _m),
        row("Contribution margin", "contribution_margin", _m),
        row("CM % of net recognized revenue (policy basis)", "cm_pct", pct),
        row("CM % of gross sale proceeds (informational)", "cm_pct_gross_sale_proceeds", pct),
        row("CM per incoming device", "margin_per_incoming_device", lambda v: f"${v:,.0f}"),
        row("CM per robot hour", "cm_per_robot_hour", lambda v: f"${v:,.0f}"),
        row("Cash required before recovery", "cash_required", _m),
        "",
        f"**Break-even resale price (base):** {('$' + format(s['base']['break_even_resale_price'], ',.0f')) if s['base']['break_even_resale_price'] else 'n/a'}  ",
        f"**Break-even sell-through (base):** {pct(s['base']['break_even_sell_through']) if s['base']['break_even_sell_through'] is not None else 'n/a'}  ",
        f"**Expected days to cash:** {s['base']['collection_days']}",
        "",
        "## Policy checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {c['label']} | {c['status'].upper()} | {c['detail']} |" for c in uw["checks"]]
    if uw["conditions"]:
        lines += ["", "## Conditions", "", *[f"- {c}" for c in uw["conditions"]]]
    if approved:
        lines += [
            "",
            "## Decision record",
            "",
            f"- Approved by **{approved['approved_by']}** on {approved['approved_at']}",
            f"- Budget version v{approved['version']} (immutable)",
            f"- Rationale: {approved['rationale']}",
        ]
    lines += [
        "",
        "---",
        f"Policy (CM% = contribution margin / net recognized revenue; illustrative synthetic thresholds): accept base CM >= "
        f"{policy.base_case_accept_cm_pct_net_revenue * 100:.0f}%, decline below {policy.base_case_review_cm_pct_net_revenue * 100:.0f}%, "
        f"downside floor {policy.downside_case_min_cm_pct_net_revenue * 100:.0f}%, ",
        f"max exceptions {policy.max_exception_rate * 100:.0f}%, WC limit {_m(float(policy.working_capital_limit))}. "
        "All figures produced by the deterministic backend engine.",
    ]
    return "\n".join(lines)
