// Calls to the backend (/api) and the local connector (/connector), via the Vite proxy.

async function request(base, path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(base + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // no JSON body
  }
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === "string" ? detail : detail ? JSON.stringify(detail) : `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return data;
}

export const backend = (path, options) => request("/api", path, options);
export const connector = (path, options) => request("/connector", path, options);

export const FOCUS_AREAS = ["numerical", "reproducibility", "data_integrity", "performance", "visualization", "all"];
export const DEPTHS = ["triage", "standard", "deep"];
export const VERIFICATION_STATUSES = ["hypothesis", "supported", "verified", "unresolved"];
export const CATEGORIES = ["error_diagnosis", "research_result", "code_change"];

export const CONNECTOR_HELP = "Start the local connector on this computer: python -m local_connector serve";

// Per-viewer convenience only; the page works without it.
export const remember = {
  get(key) {
    try {
      return sessionStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch {
      // storage unavailable
    }
  },
};
