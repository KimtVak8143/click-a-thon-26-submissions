import { describe, expect, it } from "vitest";
import { maskSensitive } from "./mask.js";

describe("maskSensitive", () => {
  it("redacts nested credentials and key-like strings", () => {
    expect(maskSensitive({ apiKey: "secret", nested: { text: "key sk-lf-abcdefgh1234" } })).toEqual({ apiKey: "***MASKED***", nested: { text: "key ***MASKED***" } });
  });
});
