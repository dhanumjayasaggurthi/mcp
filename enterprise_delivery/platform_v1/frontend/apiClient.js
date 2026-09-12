import { configureAuthentication, getAccessToken } from './auth.js';
export { configureAuthentication };

const runtime = globalThis.smarthubConfig || {};
const env = import.meta.env || {};
export const API_BASE = String(runtime.apiBaseUrl ?? env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
export const REFERENCE_MODE = String(env.VITE_REFERENCE_MODE || '').toLowerCase() === 'true';

function apiUrl(path) {
  if (!path.startsWith('/') || path.startsWith('//') || /[\r\n\\]/.test(path)) throw new Error('An API-relative path is required.');
  const base = new URL(API_BASE || location.origin, location.origin);
  if (base.username || base.password || base.search || base.hash) throw new Error('Invalid API base URL.');
  if (base.protocol !== 'https:' && !(env.DEV || ['localhost', '127.0.0.1'].includes(base.hostname))) {
    throw new Error('The API connection requires HTTPS.');
  }
  return base.href.replace(/\/$/, '') + path;
}

export async function apiFetch(path, init = {}) {
  const url = apiUrl(path);
  const headers = new Headers(init.headers || {});
  if (REFERENCE_MODE) {
    headers.set('x-subject', 'control-hub-reference-admin');
    headers.set('x-client-id', 'control-hub');
    headers.set('x-tenant', 'demo');
    headers.set('x-groups', 'data-platform-admin');
  } else {
    const token = await getAccessToken();
    if (!token) throw new ApiError('Sign in to access SmartHub.', 401);
    headers.set('Authorization', 'Bearer ' + token);
  }
  return fetch(url, { ...init, headers, credentials: 'omit', redirect: 'error', cache: 'no-store' });
}

export class ApiError extends Error {
  constructor(message, status = 0, traceId = '', retryAfter = null) {
    super(message); this.name = 'ApiError'; this.status = status;
    this.traceId = traceId; this.retryAfter = retryAfter;
  }
}
export async function requestJson(path, { body, method = 'GET', signal, ...options } = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 35000);
  try {
    const response = await apiFetch(path, { ...options, method, signal: controller.signal,
      headers: { Accept: 'application/json', ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
    let data = null;
    if (response.status !== 204) {
      const reader = response.body.getReader();
      const chunks = []; let size = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 8 * 1024 * 1024) { await reader.cancel(); throw new ApiError('Response exceeds the console display limit. Use a smaller page.', response.status); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
      const bytes = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
      try { data = JSON.parse(new TextDecoder().decode(bytes)); }
      catch { throw new ApiError('The API returned an invalid response.', response.status); }
    }
    if (!response.ok) {
      const message = typeof data?.detail === 'string' ? data.detail : 'The request could not be completed.';
      throw new ApiError(message, response.status, data?.trace_id || response.headers.get('x-trace-id') || '',
        response.headers.get('retry-after'));
    }
    return data;
  } catch (error) {
    if (error.status === 401) globalThis.dispatchEvent(new Event('smarthub:session-expired'));
    if (error.name === 'AbortError' && !signal?.aborted) throw new ApiError('The request timed out. Refresh before retrying a write.');
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
