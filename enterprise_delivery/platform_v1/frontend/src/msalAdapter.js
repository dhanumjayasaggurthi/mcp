// SmartHub-compatible account/redirect lifecycle, using this API's scopes.
export function createMsalAdapter(instance, { scopes, location, storage }) {
  const key = "edp_return_path";
  let ending = location.pathname === "/logout";
  let interactive = false;
  let pending;
  const account = () => instance.getActiveAccount();
  function savePath() {
    if (location.pathname !== "/logout") {
      try {
        storage.setItem(key, location.pathname + location.search);
      } catch {}
    }
  }
  async function signIn() {
    if (interactive) return;
    ending = false;
    interactive = true;
    savePath();
    try {
      await instance.loginRedirect({
        scopes,
        ...(account() ? { account: account() } : { prompt: "select_account" }),
      });
    } catch (error) {
      interactive = false;
      throw error;
    }
  }
  async function getAccessToken() {
    if (ending || !account()) return null;
    if (pending) return pending;
    pending = instance
      .acquireTokenSilent({ scopes, account: account() })
      .then((result) => (ending ? null : result.accessToken))
      .catch(async (error) => {
        if (
          error.name === "InteractionRequiredAuthError" ||
          [
            "login_required",
            "consent_required",
            "interaction_required",
          ].includes(error.errorCode)
        ) {
          if (!ending) await signIn();
          return null;
        }
        throw new Error("Unable to acquire the Control Hub API token.");
      })
      .finally(() => {
        pending = null;
      });
    return pending;
  }
  async function initialize() {
    await instance.initialize();
    const response = await instance.handleRedirectPromise();
    if (response?.account) instance.setActiveAccount(response.account);
    else if (!account() && instance.getAllAccounts().length === 1)
      instance.setActiveAccount(instance.getAllAccounts()[0]);
    // Never silently choose among multiple cached accounts.
    if (response?.account && !ending) {
      let path;
      try {
        path = storage.getItem(key);
        storage.removeItem(key);
      } catch {}
      if (
        path &&
        path.startsWith("/") &&
        !path.startsWith("//") &&
        !path.includes("\\") &&
        !path.startsWith("/logout")
      ) {
        const target = new URL(path, location.origin);
        if (target.origin === location.origin)
          location.replace(target.pathname + target.search);
      }
    }
  }
  async function signOut() {
    ending = true;
    // Match SmartHub's local application logout; retain the Entra SSO session.
    await instance.clearCache();
    location.replace("/logout");
  }
  return { initialize, getAccessToken, signIn, signOut };
}
