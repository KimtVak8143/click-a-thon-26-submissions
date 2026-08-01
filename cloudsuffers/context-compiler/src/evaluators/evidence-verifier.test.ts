import { describe, expect, it } from "vitest";
import { NumericEvidenceVerifier } from "./evidence-verifier.js";

const evidence = [{ id: "ev-1", sql: "SELECT 0.42", rows: [{ conversion_rate: 0.42, users: 1_250 }], checksum: "a".repeat(64), executedAt: "2026-08-02T00:00:00.000Z" }];

describe("NumericEvidenceVerifier", () => {
  it("maps percentages and formatted numbers to SQL output", () => {
    const result = new NumericEvidenceVerifier().verify("Conversion is 42% across 1,250 users.", evidence);
    expect(result.coverage).toBe(1);
    expect(result.claims.map(({ supportedBy }) => supportedBy)).toEqual([["ev-1"], ["ev-1"]]);
  });

  it("identifies unsupported numerical claims", () => {
    const result = new NumericEvidenceVerifier().verify("Conversion is 43%.", evidence);
    expect(result.coverage).toBe(0);
    expect(result.unsupported[0]?.raw).toBe("43%");
  });
});
