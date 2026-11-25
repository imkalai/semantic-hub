// @ts-check
/** Thin wrapper around fetch that turns API errors into readable messages. */

export class ApiError extends Error {}

/**
 * @param {string} path
 * @param {{method?: string, body?: any}} [opts]
 */
export async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      // X-Requested-With proves the request came from this app (the server rejects data changes without it)
      headers: { "X-Requested-With": "semantic-hub", ...(opts.body ? { "Content-Type": "application/json" } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError("Couldn't reach the server. Check your connection and try again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let detail = data.detail;
    if (Array.isArray(detail)) detail = "Please check the highlighted fields and try again.";
    throw new ApiError(detail || `Something went wrong (${res.status}).`);
  }
  return data;
}

/** Start a file download from an API path. */
export function download(path) {
  const a = document.createElement("a");
  a.href = path;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
