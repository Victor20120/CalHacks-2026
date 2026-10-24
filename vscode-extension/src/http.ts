// JSON requests to the local connector and the backend, with the server's own error message.

export class HttpError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function call<T>(base: string, path: string, options: { method?: string; body?: unknown; token?: string; timeoutMs?: number } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  const response = await fetch(base.replace(/\/+$/, "") + path, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
  });
  let data: any = null;
  try {
    data = await response.json();
  } catch {
    // no JSON body
  }
  if (!response.ok) {
    const detail = data?.detail;
    throw new HttpError(typeof detail === "string" ? detail : `HTTP ${response.status}`, response.status);
  }
  return data as T;
}

export async function isUp(base: string): Promise<boolean> {
  try {
    await call(base, "/health");
    return true;
  } catch {
    return false;
  }
}
