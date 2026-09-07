const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
const REFERENCE_MODE = String(import.meta.env.VITE_REFERENCE_MODE || '').toLowerCase() === 'true';

export async function apiFetch(path, init = {}) {
  const headers = new Headers(init.headers || {});
  if (REFERENCE_MODE) {
    headers.set('x-subject', 'control-hub-reference-admin');
    headers.set('x-client-id', 'control-hub');
    headers.set('x-tenant', 'demo');
    headers.set('x-groups', 'data-platform-admin');
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers });
}
