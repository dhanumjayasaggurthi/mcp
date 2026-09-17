const API_BASE = import.meta.env.VITE_API_BASE_URL || "";
const REFERENCE_MODE =
  String(import.meta.env.VITE_REFERENCE_MODE || "").toLowerCase() === "true";
let accessTokenProvider = null;

// The enterprise OIDC host supplies its PKCE/session token provider. Tokens
// remain in memory; this module never persists tokens or puts them in URLs.
export function configureAuthentication(provider) {
  if (typeof provider !== "function")
    throw new TypeError("Token provider must be a function");
  accessTokenProvider = provider;
}

export async function apiFetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (REFERENCE_MODE) {
    headers.set("x-subject", "control-hub-reference-admin");
    headers.set("x-client-id", "control-hub");
    headers.set("x-tenant", "demo");
    headers.set("x-groups", "data-platform-admin");
  } else {
    const provider = accessTokenProvider || globalThis.edpAuth?.getAccessToken;
    if (!provider) throw new Error("Sign in to access the Control Hub.");
    const token = await provider();
    if (!token)
      throw new Error("Your session has expired. Please sign in again.");
    headers.set("Authorization", `Bearer ${token}`);
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
