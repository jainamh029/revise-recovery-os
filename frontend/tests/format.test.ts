import { describe, expect, it } from "vitest";
import { DECISION, dateShort, money, n, pct, signedMoney, signedPct, titleCase } from "@/lib/format";

describe("format", () => {
  it("formats money with a true minus sign and optional compaction", () => {
    expect(money(1234)).toBe("$1,234");
    expect(money(-1234)).toBe("−$1,234");
    expect(money(152_480, { compact: true })).toBe("$152.5k");
    expect(money(2_400_000, { compact: true })).toBe("$2.40M");
    expect(money(12.5, { cents: true })).toBe("$12.50");
  });
  it("renders missing values as an em dash, never NaN", () => {
    expect(money(null)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(pct(Number.NaN)).toBe("—");
    expect(n(null)).toBe("—");
  });
  it("treats percentages as decimal fractions (0.25 => 25%)", () => {
    expect(pct(0.25)).toBe("25.0%");
    expect(pct(0.382, 0)).toBe("38%");
    expect(signedPct(-0.12)).toBe("−12%");
  });
  it("signs money deltas", () => {
    expect(signedMoney(9000)).toBe("+$9,000");
    expect(signedMoney(-17_200)).toBe("−$17.2k");
  });
  it("formats dates without timezone drift", () => {
    expect(dateShort("2026-10-05")).toBe("Oct 5");
    expect(dateShort(null)).toBe("—");
  });
  it("title-cases snake_case enums", () => {
    expect(titleCase("accept_with_conditions")).toBe("Accept With Conditions");
  });
  it("maps every backend decision to a label and tone", () => {
    expect(Object.keys(DECISION).sort()).toEqual(["accept", "accept_with_conditions", "decline", "review"]);
    expect(DECISION.decline.tone).toBe("bad");
  });
});
