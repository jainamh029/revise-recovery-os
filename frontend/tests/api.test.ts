import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "@/lib/api";

const respond = (status: number, body: unknown) =>
  vi.stubGlobal("fetch", vi.fn(async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status })));

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("returns parsed JSON on success", async () => {
    respond(200, { ok: 1 });
    expect(await api("/x")).toEqual({ ok: 1 });
  });
  it("surfaces domain errors from the backend verbatim (e.g. overbooking)", async () => {
    respond(409, { error: "overbooked", message: "Overbooked: Cell A has 119.5h free", details: { free_hours: 119.5 } });
    await expect(api("/x", { body: {} })).rejects.toMatchObject({ code: "overbooked", status: 409, message: expect.stringContaining("Overbooked") });
  });
  it("flattens FastAPI validation errors into one readable message", async () => {
    respond(422, { detail: [{ loc: ["body", "assumptions", "lines", 0, "exception_rate"], msg: "Input should be less than or equal to 1" }] });
    const err = await api("/x", { body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toContain("exception_rate");
  });
  it("does not crash on non-JSON error bodies", async () => {
    respond(502, "Bad gateway");
    await expect(api("/x")).rejects.toMatchObject({ status: 502, message: "Bad gateway" });
  });
  it("uses POST when a body is given and GET otherwise", async () => {
    respond(200, {});
    await api("/a");
    await api("/b", { body: { a: 1 } });
    const calls = (fetch as any).mock.calls;
    expect(calls[0][1].method).toBe("GET");
    expect(calls[1][1].method).toBe("POST");
  });
});
