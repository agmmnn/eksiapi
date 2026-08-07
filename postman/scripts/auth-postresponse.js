const AUTH_ANONYMOUS = "Get anonymous bearer token";
const AUTH_LOGIN = "Login with account";
const AUTH_REFRESH = "Refresh session";
const AUTH_REQUESTS = new Set([AUTH_ANONYMOUS, AUTH_LOGIN, AUTH_REFRESH]);
const VAULT = {
    bearerToken: "eksi-bearer-token",
    clientSecret: "eksi-client-secret",
    refreshToken: "eksi-refresh-token",
    expiresAt: "eksi-token-expires-at",
    accountNick: "eksi-account-nick",
};

function tokenData(payload) {
    return payload?.Data && typeof payload.Data === "object" ? payload.Data : payload;
}

if (AUTH_REQUESTS.has(pm.info.requestName)) {
    if (pm.response.code < 200 || pm.response.code >= 300) {
        throw new Error(`Authentication failed with HTTP ${pm.response.code}.`);
    }
    const data = tokenData(pm.response.json());
    const accessToken = data?.access_token || data?.AccessToken;
    const clientSecret = pm.variables.get("authClientSecret");
    if (!accessToken || !clientSecret) {
        throw new Error("Authentication response did not contain the expected session values.");
    }
    await pm.vault.set(VAULT.bearerToken, accessToken);
    await pm.vault.set(VAULT.clientSecret, clientSecret);
    const refreshToken = data?.refresh_token || data?.RefreshToken;
    if (refreshToken) {
        await pm.vault.set(VAULT.refreshToken, refreshToken);
    } else if (pm.info.requestName === AUTH_ANONYMOUS || pm.info.requestName === AUTH_LOGIN) {
        await pm.vault.unset(VAULT.refreshToken);
        await pm.vault.unset(VAULT.accountNick);
    }
    const expiresIn = Number(data?.expires_in ?? data?.ExpiresIn);
    if (Number.isFinite(expiresIn)) {
        await pm.vault.set(VAULT.expiresAt, String(Date.now() + expiresIn * 1000));
    }
    const accountNick = data?.userName || data?.username || data?.nick || data?.Nick;
    if (accountNick) {
        await pm.vault.set(VAULT.accountNick, String(accountNick));
    }
    pm.variables.set("bearerToken", accessToken);
    pm.variables.set("clientSecret", clientSecret);
    console.log(pm.info.requestName === AUTH_ANONYMOUS ? "Anonymous Postman session is ready." : "Account Postman session is ready.");
}
