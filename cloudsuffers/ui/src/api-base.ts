const configuredBaseUrl = (
  import.meta.env.VITE_COMPILER_API_URL?.trim() || "http://localhost:8000"
).replace(/\/$/, "");

export function compilerApiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${configuredBaseUrl}${normalizedPath}`;
}
