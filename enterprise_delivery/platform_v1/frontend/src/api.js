import { apiFetch } from "../apiClient";
export async function request(path, { body, method = "GET", signal } = {}) {
  const res = await apiFetch(path, {
    method,
    signal,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  if (res.status !== 204) {
    try {
      data = await res.json();
    } catch {
      throw new Error("The server returned an unreadable response");
    }
  }
  if (!res.ok) {
    const error = new Error(
      typeof data?.detail === "string"
        ? data.detail
        : `Request failed (${res.status})`,
    );
    error.status = res.status;
    error.trace = data?.trace_id;
    throw error;
  }
  return data;
}
export const control = "/v1/control";
export const queryOptions = (key, path, enabled = true) => ({
  queryKey: key,
  queryFn: ({ signal }) => request(path, { signal }),
  enabled,
  retry: (count, e) => count < 1 && e.status >= 500,
});
export function params(values) {
  const p = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") p.set(k, v);
  });
  return p.toString();
}
export function downloadJson(name, data) {
  const u = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = u;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
