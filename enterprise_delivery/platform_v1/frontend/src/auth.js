import {
  UserManager,
  WebStorageStateStore,
  InMemoryWebStorage,
} from "oidc-client-ts";
import { configureAuthentication } from "../apiClient";
const authority = import.meta.env.VITE_OIDC_AUTHORITY;
const client_id = import.meta.env.VITE_OIDC_CLIENT_ID;
export const oidc =
  authority && client_id
    ? new UserManager({
        authority,
        client_id,
        response_type: "code",
        scope: import.meta.env.VITE_OIDC_SCOPE || "openid profile edp:admin",
        redirect_uri: new URL("/auth/callback", location.origin).href,
        post_logout_redirect_uri: location.origin,
        automaticSilentRenew: true,
        userStore: new WebStorageStateStore({
          store: new InMemoryWebStorage(),
        }),
      })
    : null;
export async function initializeAuth() {
  if (oidc) {
    if (location.pathname === "/auth/callback") {
      await oidc.signinRedirectCallback();
      history.replaceState({}, "", "/");
    }
    configureAuthentication(async () => {
      const user = await oidc.getUser();
      return user && !user.expired ? user.access_token : null;
    });
  }
}
export function signIn() {
  if (globalThis.edpAuth?.signIn) return globalThis.edpAuth.signIn();
  if (oidc) return oidc.signinRedirect();
  throw new Error(
    "Configure the enterprise OIDC host or VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID.",
  );
}
export async function signOut() {
  if (globalThis.edpAuth?.signOut) return globalThis.edpAuth.signOut();
  if (oidc) {
    await oidc.removeUser();
    await oidc.signoutRedirect();
  }
}
