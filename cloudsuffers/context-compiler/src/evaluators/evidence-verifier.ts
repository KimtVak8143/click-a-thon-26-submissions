import type { EvidenceRecord } from "../types/domain.js";

export interface NumericClaim {
  readonly raw: string;
  readonly value: number;
  readonly percent: boolean;
  readonly supportedBy: readonly string[];
}

export interface EvidenceVerification {
  readonly claims: readonly NumericClaim[];
  readonly unsupported: readonly NumericClaim[];
  readonly coverage: number;
}

const NUMBER_PATTERN = /(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?\s*%?/g;

function numericValues(value: unknown, output: number[]): void {
  if (typeof value === "number" && Number.isFinite(value)) output.push(value);
  else if (typeof value === "string") {
    for (const match of value.matchAll(NUMBER_PATTERN)) {
      const parsed = Number(match[0].replaceAll(",", "").replace("%", "").trim());
      if (Number.isFinite(parsed)) output.push(parsed);
    }
  } else if (Array.isArray(value)) {
    for (const child of value) numericValues(child, output);
  } else if (value !== null && typeof value === "object") {
    for (const child of Object.values(value as Record<string, unknown>)) numericValues(child, output);
  }
}

function matchesClaim(claim: NumericClaim, candidate: number): boolean {
  const alternatives = claim.percent ? [claim.value, claim.value / 100] : [claim.value];
  return alternatives.some((expected) => Math.abs(expected - candidate) <= Math.max(1e-9, Math.abs(expected) * 1e-6));
}

export class NumericEvidenceVerifier {
  verify(text: string, evidence: readonly EvidenceRecord[]): EvidenceVerification {
    const claims = [...text.matchAll(NUMBER_PATTERN)].map((match) => {
      const raw = match[0].trim();
      return { raw, value: Number(raw.replaceAll(",", "").replace("%", "")), percent: raw.endsWith("%") };
    });
    const verified = claims.map((claim) => {
      const supportedBy = evidence.filter((item) => {
        const values: number[] = [];
        numericValues(item.rows, values);
        return values.some((candidate) => matchesClaim({ ...claim, supportedBy: [] }, candidate));
      }).map((item) => item.id);
      return { ...claim, supportedBy };
    });
    const unsupported = verified.filter((claim) => claim.supportedBy.length === 0);
    return { claims: verified, unsupported, coverage: verified.length === 0 ? 1 : (verified.length - unsupported.length) / verified.length };
  }
}
