const sensitiveKey = /(api[-_]?key|authorization|cookie|password|secret|token)/i;
const secretPattern = /\b(?:sk|pk)-(?:lf-)?[A-Za-z0-9_-]{8,}\b/g;

export function maskSensitive(value: unknown, key = ""): unknown {
  if (sensitiveKey.test(key)) return "***MASKED***";
  if (typeof value === "string") return value.replace(secretPattern, "***MASKED***").slice(0, 10_000);
  if (Array.isArray(value)) return value.map((child) => maskSensitive(child));
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([childKey, child]) => [childKey, maskSensitive(child, childKey)]));
  }
  return value;
}
