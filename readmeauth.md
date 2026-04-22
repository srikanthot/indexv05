/**
 * Azure Entra ID (MSAL) configuration.
 *
 * All values are populated from NEXT_PUBLIC_ environment variables so the
 * same codebase works for local dev (no auth), local Entra testing, and
 * Azure App Service deployment — controlled entirely by configuration.
 *
 * ── Environment variables ─────────────────────────────────────────
 *
 *  NEXT_PUBLIC_CLIENT_ID       — App Registration client/application ID
 *  NEXT_PUBLIC_AUTHORITY       — Entra authority URL, e.g.:
 *                                 Public cloud:  https://login.microsoftonline.com/<tenant-id>
 *                                 GCC High:      https://login.microsoftonline.us/<tenant-id>
 *  NEXT_PUBLIC_REDIRECT_URI    — Post-login redirect (default: "/")
 *  NEXT_PUBLIC_CLOUD_INSTANCE  — (Optional) Override cloud instance for sovereign clouds.
 *                                 Defaults to "" (auto-detected from authority).
 *  NEXT_PUBLIC_API_SCOPE       — (Optional) Backend API scope for token acquisition, e.g.:
 *                                 "api://<client-id>/access_as_user"
 *                                 If blank, only "User.Read" is requested.
 *  NEXT_PUBLIC_ALLOWED_GROUP   — (Optional) Entra security group Object ID.
 *                                 If set, only members of this group can access the app.
 *                                 Leave blank to allow all tenant users.
 *
 * When NEXT_PUBLIC_CLIENT_ID and NEXT_PUBLIC_AUTHORITY are both blank,
 * the app falls back to debug mode (X-Debug-User-Id headers).
 */

import {
  type Configuration,
  PublicClientApplication,
  EventType,
  type EventMessage,
  type AuthenticationResult,
} from "@azure/msal-browser";

// Extract the authority host (e.g. "login.microsoftonline.us") so MSAL
// skips public-cloud instance discovery, which times out for GCC High.
const _authority = process.env.NEXT_PUBLIC_AUTHORITY ?? "";
const _cloudInstance = process.env.NEXT_PUBLIC_CLOUD_INSTANCE ?? "";
function _extractAuthorityHost(authority: string): string {
  try {
    return new URL(authority).hostname;
  } catch {
    return "";
  }
}
const _knownHost = _cloudInstance || _extractAuthorityHost(_authority);

export const msalConfig: Configuration = {
  auth: {
    clientId: process.env.NEXT_PUBLIC_CLIENT_ID ?? "",
    authority: _authority,
    redirectUri: process.env.NEXT_PUBLIC_REDIRECT_URI ?? "/",
    postLogoutRedirectUri: "/",
    knownAuthorities: _knownHost ? [_knownHost] : [],
  },
  cache: {
    cacheLocation: "localStorage"
  },
};

// ── MSAL Instance (singleton) ─────────────────────────────────────
// Shared across AuthGate and api.ts to avoid creating multiple
// PublicClientApplication instances that fight over sessionStorage.

let _msalInstance: PublicClientApplication | null = null;

export function getMsalInstance(): PublicClientApplication | null {
  /* We can only acquire the token client side.  Ensure
  we are not doing server side rendering */
  if (typeof window === "undefined") return null; // MSAL fix #1

  if (!_msalInstance) {
    validateMsalCache();
    _msalInstance = new PublicClientApplication(msalConfig);
  }

  // Set the first active account after redirect completes
  _msalInstance?.addEventCallback((event: EventMessage) => {
    if (
      event.eventType === EventType.LOGIN_SUCCESS &&
      (event.payload as AuthenticationResult)?.account
    ) {
      _msalInstance!.setActiveAccount(
        (event.payload as AuthenticationResult).account
      );
    }
  });

  return _msalInstance;
}

export function clearMsalCache(): void {
  const keysToRemove = [];

  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);

    if (
      key?.startsWith("msal.") ||
      key?.startsWith("msal-browser.") ||
      key?.startsWith("msal")
    ) {
      keysToRemove.push(key);
    }
  }

  keysToRemove.forEach((key) => localStorage.removeItem(key));
}

function isProbablyBase64(str: string) {
  return /^[A-Za-z0-9+/=]+$/.test(str);
}

// Bonus MSAL fix, verify the MSAL cache is not corrupt before letting MSAL use it
/**
 * Checks JWT-like values (ID token, Access Token)
 * Checks valid JSON
 * Checks redirect state keys length (nonce, state, request)
 * @returns void
 */
export function validateMsalCache() {
  try {
    for (const key in localStorage) {
      if (key.includes("msal") && key.trim() !== "msal.version") {
        const value = localStorage.getItem(key);
        if (!value) continue;

        // 1.  Try to decode JWT-like values
        if (value.includes(".")) {
          const parts = value.split(".");
          for (const p of parts) {
            if (p && isProbablyBase64(p)) atob(p); // throws on invalid base64
          }
        }

        // 2. Validate JSON values
        if (value.trim().startsWith("{") || value.trim().startsWith("[")) {
          JSON.parse(value);
        }

        // 3. Validate known redirect state values
        if (key.includes("request") ||
          key.includes("state") ||
          key.includes("nonce")) {
          if (typeof value !== "string" || value.length < 5) {
            throw new Error("Invalid MSAL redirect state");
          }
        }
      }
    }
  } catch (err) {
    console.warn("Corrupted MSAL cache detected - clearing: ", err);
    clearMsalCache();
  }
}

const apiScope = process.env.NEXT_PUBLIC_API_SCOPE ?? "";

/**
 * Scopes requested at interactive sign-in.
 * Only Graph/profile scopes here — mixing API scopes from a different
 * resource in the same request causes MSAL to return a Graph token
 * instead of an API token.
 */
export const loginRequest = {
  scopes: ["User.Read"],
};

/**
 * Scopes requested when calling the backend API.
 * Separate resource → separate access token with correct audience.
 * Used by api.ts in acquireTokenSilent().
 */
export const apiRequest = {
  scopes: apiScope ? [apiScope] : [],
};

/**
 * Optional group-based access restriction.
 * When set, AuthGate checks the user's group membership.
 */
export const allowedGroupId = process.env.NEXT_PUBLIC_ALLOWED_GROUP ?? "";

/**
 * Returns true if Entra ID env vars are configured.
 * Use this to decide whether to enable real auth or fall back to debug mode.
 */
export function isEntraConfigured(): boolean {
  return Boolean(msalConfig.auth.clientId && msalConfig.auth.authority);
}
