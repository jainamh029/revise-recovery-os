"""Synthetic, illustrative assumptions. Not Revise Robotics data."""

# (manufacturer, model, category age, process_min, fpy, exc, repair, resale, sell_through)
MODELS = [
    ("Dell", "Latitude 5490", 4.5, 30, 0.88, 0.10, 55, 255, 0.92),
    ("Lenovo", "ThinkPad T480", 4.2, 32, 0.90, 0.09, 50, 290, 0.93),
    ("HP", "EliteBook 840 G5", 4.4, 31, 0.87, 0.11, 60, 270, 0.91),
    ("Apple", "MacBook Air 13 (2015)", 6.0, 38, 0.84, 0.14, 95, 330, 0.90),
    ("Apple", "MacBook Pro 13 (2017)", 4.8, 40, 0.82, 0.16, 140, 480, 0.88),
    ("Microsoft", "Surface Laptop 2", 3.6, 36, 0.80, 0.18, 160, 310, 0.86),
    ("Dell", "Latitude 7420", 2.0, 30, 0.93, 0.06, 45, 610, 0.95),
    ("Lenovo", "ThinkPad X1 Carbon Gen 9", 2.2, 32, 0.94, 0.05, 50, 780, 0.95),
    ("HP", "ProBook 450 G3", 6.2, 34, 0.76, 0.22, 75, 140, 0.78),
    ("Acer", "Aspire E15 (2014)", 7.5, 42, 0.62, 0.34, 110, 85, 0.66),
    ("Lenovo", "IdeaPad 110", 6.4, 45, 0.58, 0.38, 120, 70, 0.62),
    ("Dell", "Inspiron 15 3000", 5.9, 44, 0.64, 0.32, 100, 95, 0.68),
]

DISCOUNT_RATE = 0.015  # illustrative promotional discount as a share of gross sale proceeds

PARTNERS = {
    "edu": dict(
        name="Cascadia State University",
        partner_type="university",
        geography="Pacific Northwest, US",
        contact_name="Dana Whitfield",
        contact_email="it-surplus@example.edu",
        payment_terms_days=14,
        expected_monthly_volume=450,
        data_security_requirement="standard",
        sourcing_method="Campus refresh programme",
        logistics_method="Freight pickup, palletised",
        notes="Synthetic partner. 3-year-old fleet refreshes each spring and fall.",
    ),
    "itad": dict(
        name="Meridian ITAD Services",
        partner_type="itad_provider",
        geography="Midwest, US",
        contact_name="Raúl Ortega",
        contact_email="lots@example-itad.com",
        payment_terms_days=30,
        expected_monthly_volume=900,
        data_security_requirement="enhanced",
        sourcing_method="Mixed lots from enterprise customers",
        logistics_method="Weekly LTL",
        notes="Synthetic partner. High volume, mixed grade, limited pre-sorting.",
    ),
    "ent": dict(
        name="Northbridge Financial Group",
        partner_type="enterprise_device_supplier",
        geography="Northeast, US",
        contact_name="Priya Nair",
        contact_email="asset-recovery@example-bank.com",
        payment_terms_days=60,
        expected_monthly_volume=300,
        data_security_requirement="enhanced",
        sourcing_method="Enterprise lifecycle refresh",
        logistics_method="White-glove pickup",
        notes="Synthetic partner. Newer premium devices; slow-paying accounts payable.",
    ),
    "rec": dict(
        name="GreenLoop Recycling Co.",
        partner_type="recycler",
        geography="Southwest, US",
        contact_name="Marcus Bell",
        contact_email="intake@example-recycler.com",
        payment_terms_days=30,
        expected_monthly_volume=1200,
        data_security_requirement="standard",
        sourcing_method="Bulk e-waste intake",
        logistics_method="Gaylord boxes, backhaul",
        notes="Synthetic partner. Oldest, most damaged devices.",
    ),
    "atlas": dict(
        name="Atlas Wholesale (B2B resale)",
        partner_type="resale_channel",
        geography="National",
        contact_name="Jin Park",
        contact_email="buying@example-wholesale.com",
        payment_terms_days=30,
        expected_monthly_volume=None,
        data_security_requirement="standard",
        sourcing_method=None,
        logistics_method="Pallet shipment",
        notes="Synthetic bulk buyer used for enterprise-grade lots.",
    ),
}


def lines(spec: list[tuple[int, int, str]], overrides: dict[int, dict] | None = None):
    """spec: (model index, units, grade)."""
    out = []
    for idx, units, grade in spec:
        m = MODELS[idx]
        ln = dict(
            model_name=f"{m[0]} {m[1]}",
            units=units,
            condition_grade=grade,
            process_minutes=m[3],
            first_pass_yield=m[4],
            exception_rate=m[5],
            repair_cost=m[6],
            resale_price=m[7],
            sell_through=m[8],
            _idx=idx,
        )
        ln.update((overrides or {}).get(idx, {}))
        out.append(ln)
    return out


COHORTS = {
    "EDU-001": dict(
        name="Cascadia fall refresh -- 500 laptops",
        partner="edu",
        contract_type="supply_purchase",
        ownership="operator_owned",
        acq=70,
        inbound=3.5,
        ship=13,
        fee=0.12,
        ret=0.04,
        payout_days=7,
        sell_days=45,
        lines=lines([(0, 180, "B"), (1, 170, "B"), (2, 100, "B"), (3, 50, "B")]),
    ),
    "ITAD-002": dict(
        name="Meridian mixed lot -- 750 laptops",
        partner="itad",
        contract_type="supply_purchase",
        ownership="operator_owned",
        acq=31,
        inbound=6,
        ship=14,
        fee=0.12,
        ret=0.05,
        payout_days=7,
        sell_days=45,
        lines=lines(
            [(2, 220, "B"), (0, 200, "C"), (8, 150, "C"), (5, 80, "B"), (11, 100, "C")],
            {
                2: dict(resale_price=240, sell_through=0.85, first_pass_yield=0.80),
                0: dict(resale_price=220, sell_through=0.84, first_pass_yield=0.78),
                8: dict(resale_price=140, sell_through=0.80, first_pass_yield=0.72),
                5: dict(resale_price=295, sell_through=0.85, first_pass_yield=0.74),
                11: dict(resale_price=112, sell_through=0.74, exception_rate=0.18, first_pass_yield=0.70),
            },
        ),
    ),
    "ENT-003": dict(
        name="Northbridge hardware refresh -- 300 laptops",
        partner="ent",
        contract_type="hybrid",
        ownership="operator_owned",
        acq=210,
        inbound=8,
        ship=18,
        fee=0.10,
        ret=0.03,
        payout_days=30,
        sell_days=30,
        service_fee=14,
        revenue_share=0,
        lines=lines([(6, 140, "A"), (7, 90, "A"), (4, 70, "B")]),
    ),
    "REC-004": dict(
        name="GreenLoop bulk intake -- 600 laptops",
        partner="rec",
        contract_type="supply_purchase",
        ownership="operator_owned",
        acq=28,
        inbound=5,
        ship=12,
        fee=0.12,
        ret=0.08,
        payout_days=7,
        sell_days=45,
        recovery=0.5,
        lines=lines([(9, 220, "D"), (10, 200, "D"), (11, 180, "C")]),
    ),
    "ITAD-006": dict(
        name="Meridian mixed lot #2 -- 900 laptops",
        partner="itad",
        contract_type="supply_purchase",
        ownership="operator_owned",
        acq=31,
        inbound=6,
        ship=14,
        fee=0.12,
        ret=0.05,
        payout_days=7,
        sell_days=45,
        lines=lines(
            [(2, 264, "B"), (0, 240, "C"), (8, 180, "C"), (5, 96, "B"), (11, 120, "C")],
            {
                2: dict(resale_price=240, sell_through=0.85, first_pass_yield=0.80),
                0: dict(resale_price=220, sell_through=0.84, first_pass_yield=0.78),
                8: dict(resale_price=140, sell_through=0.80, first_pass_yield=0.72),
                5: dict(resale_price=295, sell_through=0.85, first_pass_yield=0.74),
                11: dict(resale_price=112, sell_through=0.74, exception_rate=0.18, first_pass_yield=0.70),
            },
        ),
    ),
    "EDU-005": dict(
        name="Cascadia spring retirement -- 400 laptops",
        partner="edu",
        contract_type="supply_purchase",
        ownership="operator_owned",
        acq=64,
        inbound=3.5,
        ship=13,
        fee=0.12,
        ret=0.04,
        payout_days=7,
        sell_days=45,
        lines=lines([(1, 120, "B"), (0, 120, "B"), (2, 90, "B"), (5, 40, "B"), (3, 30, "B")]),
    ),
}


def payload(code: str) -> dict:
    c = COHORTS[code]
    return {
        "lines": [{k: v for k, v in ln.items() if k != "_idx"} for ln in c["lines"]],
        "acquisition_cost_per_unit": c["acq"],
        "inbound_logistics_per_unit": c["inbound"],
        "outbound_shipping_per_unit": c["ship"],
        "marketplace_fee_rate": c["fee"],
        "discount_rate": DISCOUNT_RATE,
        "return_rate": c["ret"],
        "payout_days": c["payout_days"],
        "days_to_sale": c["sell_days"],
        "service_fee_per_unit": c.get("service_fee", 0),
        "repair_recovery_rate": c.get("recovery", 0.85),
        "contract_type": c["contract_type"],
        "ownership": c["ownership"],
    }
