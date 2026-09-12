// OAuth public-client PKCE. Credentials remain in memory; only the short-lived
// authorization transaction is kept in sessionStorage across the SSO redirect.
const runtime = globalThis.smarthubConfig || {};
let sessionToken = null;
let refreshToken = null;
let expiresAt = 0;
let refreshPromise = null;
let hostProvider = null;
const TX_KEY = 'smarthub.oauth.transaction';

function endpoint(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' && !(['localhost', '127.0.0.1'].includes(url.hostname) && url.protocol === 'http:')) {
    throw new Error('Identity endpoints require HTTPS.');
  }
  if (url.username || url.password || url.hash) throw new Error('Invalid identity endpoint.');
  return url;
}
function authConfig() {
  const value = runtime.auth;
  if (!value?.clientId || !value.authorizationEndpoint || !value.tokenEndpoint) {
    throw new Error('Single sign-on is not configured. Use an access token or configure the identity provider.');
  }
  endpoint(value.authorizationEndpoint);
  endpoint(value.tokenEndpoint);
  const redirect = new URL(value.redirectUri || location.origin + location.pathname);
  if (redirect.origin !== location.origin || redirect.search || redirect.hash) throw new Error('The SSO callback must be on this console origin without query parameters.');
  return { ...value, redirectUri: redirect.href };
}
function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
async function exchange(parameters) {
  const conf = authConfig();
  const response = await fetch(conf.tokenEndpoint, {
    method: 'POST', credentials: 'omit', redirect: 'error',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: conf.clientId, ...parameters }),
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error('Identity provider rejected this session. Sign in again.');
  const body = await response.json();
  if (typeof body.access_token !== 'string' || !body.access_token || String(body.token_type).toLowerCase() !== 'bearer') {
    throw new Error('The identity provider did not return a bearer access token.');
  }
  const seconds = Number(body.expires_in);
  if (!Number.isFinite(seconds) || seconds <= 0) throw new Error('The identity provider returned an invalid token lifetime.');
  sessionToken = body.access_token;
  refreshToken = body.refresh_token || refreshToken;
  expiresAt = Date.now() + seconds * 1000;
}
export function configureAuthentication(provider) {
  if (typeof provider !== 'function') throw new TypeError('Token provider must be a function');
  hostProvider = provider;
}
export function useAccessToken(token) {
  if (typeof token !== 'string' || !token.trim() || token.length > 32768 || /[\r\n]/.test(token)) {
    throw new Error('Enter a valid bearer access token.');
  }
  sessionToken = token.trim();
  refreshToken = null;
  expiresAt = Infinity; // Server validates expiry and authorization on every call.
}
export function clearAuthentication() {
  sessionToken = null;
  refreshToken = null;
  expiresAt = 0;
  sessionStorage.removeItem(TX_KEY);
}
export function canUseSSO() {
  return Boolean(runtime.auth?.clientId || globalThis.edpAuth?.signIn);
}
export async function signIn() {
  if (globalThis.edpAuth?.signIn) return globalThis.edpAuth.signIn();
  const conf = authConfig();
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
  sessionStorage.setItem(TX_KEY, JSON.stringify({ verifier, state, createdAt: Date.now(), redirectUri: conf.redirectUri }));
  const url = endpoint(conf.authorizationEndpoint);
  Object.entries({ response_type: 'code', client_id: conf.clientId, redirect_uri: conf.redirectUri,
    scope: conf.scope || 'openid profile edp:admin', state, code_challenge: challenge,
    code_challenge_method: 'S256' }).forEach(([key, value]) => url.searchParams.set(key, value));
  location.assign(url.href);
}
let callbackPromise = null;
export function completeSignIn() {
  if (callbackPromise) return callbackPromise;
  const parameters = new URLSearchParams(location.search);
  if (!parameters.has('code') && !parameters.has('error')) return Promise.resolve();
  callbackPromise = (async () => {
    const raw = sessionStorage.getItem(TX_KEY);
    sessionStorage.removeItem(TX_KEY);
    history.replaceState({}, '', location.pathname + location.hash);
    const tx = raw ? JSON.parse(raw) : null;
    if (!tx || tx.state !== parameters.get('state') || Date.now() - tx.createdAt > 300000) {
      throw new Error('Sign-in state expired or did not match. Start sign-in again.');
    }
    if (parameters.has('error')) throw new Error('Sign-in was not completed at the identity provider.');
    await exchange({ grant_type: 'authorization_code', code: parameters.get('code'),
      code_verifier: tx.verifier, redirect_uri: tx.redirectUri });
  })();
  return callbackPromise;
}
export async function getAccessToken() {
  if (hostProvider) return hostProvider();
  if (sessionToken && Date.now() < expiresAt - 30000) return sessionToken;
  if (refreshToken) {
    refreshPromise ||= exchange({ grant_type: 'refresh_token', refresh_token: refreshToken })
      .catch(error => { clearAuthentication(); throw error; })
      .finally(() => { refreshPromise = null; });
    await refreshPromise;
    return sessionToken;
  }
  if (globalThis.edpAuth?.getAccessToken) return globalThis.edpAuth.getAccessToken();
  return null;
}
