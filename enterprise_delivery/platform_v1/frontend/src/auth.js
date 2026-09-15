import { PublicClientApplication } from "@azure/msal-browser";
import { createMsalAdapter } from "./msalAdapter";
import {
  UserManager,
  WebStorageStateStore,
  InMemoryWebStorage,
} from "oidc-client-ts";
import { configureAuthentication } from "../apiClient";
let msal;
const msalClientId = import.meta.env.VITE_MSAL_CLIENT_ID;
const msalAuthority = import.meta.env.VITE_MSAL_AUTHORITY;
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
  if (globalThis.edpAuth?.getAccessToken) return;
  if (msalClientId || msalAuthority) {
    const scopes = (import.meta.env.VITE_CONTROL_HUB_API_SCOPES || "")
      .split(/\s+/)
      .filter(Boolean);
    if (
      !msalClientId ||
      !msalAuthority ||
      !scopes.length ||
      scopes.some((s) => !s.startsWith("api://") && !s.startsWith("https://"))
    ) {
      throw new Error(
        "Configure the MSAL client, authority and API-specific scopes.",
      );
    }
    msal = createMsalAdapter(
      new PublicClientApplication({
        auth: {
          clientId: msalClientId,
          authority: msalAuthority,
          redirectUri: location.origin,
          postLogoutRedirectUri: location.origin + "/logout",
          navigateToLoginRequestUrl: false,
        },
        cache: { cacheLocation: "localStorage" },
        system: {
          loggerOptions: { piiLoggingEnabled: false, loggerCallback: () => {} },
        },
      }),
      { scopes, location, storage: sessionStorage },
    );
    await msal.initialize();
    configureAuthentication(msal.getAccessToken);
    return;
  }
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
  if (msal) return msal.signIn();
  if (oidc) return oidc.signinRedirect();
  throw new Error(
    "Configure the enterprise OIDC host or VITE_OIDC_AUTHORITY and VITE_OIDC_CLIENT_ID.",
  );
}
export async function signOut() {
  if (globalThis.edpAuth?.signOut) return globalThis.edpAuth.signOut();
  if (msal) return msal.signOut();
  if (oidc) {
    await oidc.removeUser();
    await oidc.signoutRedirect();
  }
}
