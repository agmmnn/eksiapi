const AUTH_ANONYMOUS = "Get anonymous bearer token";
const AUTH_LOGIN = "Login with account";
const AUTH_REFRESH = "Refresh session";
const BASE_URL = pm.variables.get("baseUrl") || "https://api.eksisozluk.com";
const USER_AGENT = "eksisozluk-android/144";
let forgeLibrary;
const PUBLIC_KEY = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4cNO1MGajB7fTxuZ1bC+
lSwMuob7YgTH441nWgTA+BDlw5bdYGyAIrTCkaSLrwimgG5rHT2izPqzn1rGRoqm
OV2VwIMkTF0FwmZ+STDu09zF2y7y7/OkZ9FaNOQTBDoCS1t2z38WC6YwzA4b/GTr
c/FFfMnVw4GPgIWlsxkNYMIspbtLEWcQGaa76e1nGPWxKgN0vF6T2lvhJaHnva9s
La9v+V2gcIlELF2KyIbNaN0zoy0bna7Mh1FA8Z/8BFPH2aIIdvvhIycZHcISZdsd
8giHsXSYkZlOqP7JS8ChKgWUccPNQlI+n7NbxmIGIFfWPXFIOc5sWbNQ+RtrLYrJ
owIDAQAB
-----END PUBLIC KEY-----`;
const VAULT = {
    username: "eksi-username",
    password: "eksi-password",
    bearerToken: "eksi-bearer-token",
    clientSecret: "eksi-client-secret",
    refreshToken: "eksi-refresh-token",
    clientUniqueId: "eksi-client-unique-id",
    expiresAt: "eksi-token-expires-at",
};

function randomInteger(minimum, maximum) {
    const range = maximum - minimum + 1;
    const limit = Math.floor(0x100000000 / range) * range;
    const values = new Uint32Array(1);
    do {
        crypto.getRandomValues(values);
    } while (values[0] >= limit);
    return minimum + (values[0] % range);
}

function randomHex(length) {
    const bytes = new Uint8Array(Math.ceil(length / 2));
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("").slice(0, length);
}

function randomUuid() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function generateApiSecret(serverTime, clientSecret) {
    const forge = forgeLibrary ??= pm.require("npm:node-forge@1.4.0");
    const dayOffset = randomInteger(1, 5000);
    const hourOffset = randomInteger(1, 5000);
    const minuteOffset = randomInteger(1, 10000);
    const hexLength = randomInteger(40, 80);
    const adjustedTime = serverTime - dayOffset * 86400000 - hourOffset * 3600000 - minuteOffset * 60000;
    const plaintext = `${randomHex(hexLength)}-c8ecd738-dc33-45a4-a977-ae8e2a51c644-${hexLength * hexLength}-${adjustedTime}-${dayOffset}-${hourOffset}-${minuteOffset}-eksisozluk-android/144-${clientSecret}`;
    const publicKey = forge.pki.publicKeyFromPem(PUBLIC_KEY);
    const encrypted = publicKey.encrypt(forge.util.encodeUtf8(plaintext), "RSAES-PKCS1-V1_5");
    return forge.util.encode64(encrypted);
}

async function getServerTime() {
    const response = await pm.sendRequest({
        url: `${BASE_URL}/v2/clientsettings/time`,
        method: "GET",
        header: {Accept: "application/json", "User-Agent": "eksisozluk-android/144"},
    });
    if (response.code < 200 || response.code >= 300) {
        throw new Error(`Server time request failed with HTTP ${response.code}.`);
    }
    const serverTime = Number(response.json()?.Data);
    if (!Number.isFinite(serverTime)) {
        throw new Error("Server time response did not contain a valid Data value.");
    }
    return serverTime;
}

function tokenFromPayload(payload) {
    const data = payload?.Data && typeof payload.Data === "object" ? payload.Data : payload;
    return data?.access_token || data?.AccessToken;
}

async function getClientUniqueId() {
    const stored = await pm.vault.get(VAULT.clientUniqueId);
    if (stored) {
        return stored;
    }
    const generated = randomUuid();
    await pm.vault.set(VAULT.clientUniqueId, generated);
    return generated;
}

async function issueAnonymousToken(clientUniqueId) {
    const clientSecret = randomUuid();
    const apiSecret = generateApiSecret(await getServerTime(), clientSecret);
    const response = await pm.sendRequest({
        url: `${BASE_URL}/v2/account/anonymoustoken`,
        method: "POST",
        header: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Client-Secret": clientSecret,
            "User-Agent": "eksisozluk-android/144",
        },
        body: {
            mode: "urlencoded",
            urlencoded: [
                {key: "DeviceModel", value: "Google sdk_gphone_x86_64"},
                {key: "Platform", value: "g"},
                {key: "Version", value: "2.4.10"},
                {key: "Build", value: "144"},
                {key: "Api-Secret", value: apiSecret},
                {key: "Client-Secret", value: clientSecret},
                {key: "ClientUniqueId", value: clientUniqueId},
            ],
        },
    });
    if (response.code < 200 || response.code >= 300) {
        throw new Error(`Anonymous authentication failed with HTTP ${response.code}.`);
    }
    const payload = response.json();
    const accessToken = tokenFromPayload(payload);
    if (!accessToken) {
        throw new Error("Anonymous authentication response did not contain an access token.");
    }
    return {accessToken, clientSecret, payload};
}

function setAuthRequestVariables({accessToken, apiSecret, clientSecret, clientUniqueId, refreshToken, username, password}) {
    if (accessToken) pm.variables.set("bearerToken", accessToken);
    if (apiSecret) pm.variables.set("apiSecret", apiSecret);
    if (clientSecret) {
        pm.variables.set("clientSecret", clientSecret);
        pm.variables.set("authClientSecret", clientSecret);
    }
    if (clientUniqueId) pm.variables.set("clientUniqueId", clientUniqueId);
    if (refreshToken) pm.variables.set("refreshToken", refreshToken);
    if (username) pm.variables.set("authUsername", username);
    if (password) pm.variables.set("authPassword", password);
}

async function prepareAnonymousRequest() {
    const clientUniqueId = await getClientUniqueId();
    const clientSecret = randomUuid();
    setAuthRequestVariables({
        apiSecret: generateApiSecret(await getServerTime(), clientSecret),
        clientSecret,
        clientUniqueId,
    });
}

async function prepareLoginRequest() {
    const username = await pm.vault.get(VAULT.username);
    const password = await pm.vault.get(VAULT.password);
    if (!username || !password) {
        throw new Error("Add eksi-username and eksi-password to Postman Local Vault before login.");
    }
    const clientUniqueId = await getClientUniqueId();
    const anonymous = await issueAnonymousToken(clientUniqueId);
    const clientSecret = randomUuid();
    setAuthRequestVariables({
        accessToken: anonymous.accessToken,
        apiSecret: generateApiSecret(await getServerTime(), clientSecret),
        clientSecret,
        clientUniqueId,
        username,
        password,
    });
}

async function prepareRefreshRequest() {
    const accessToken = await pm.vault.get(VAULT.bearerToken);
    const clientSecret = await pm.vault.get(VAULT.clientSecret);
    const refreshToken = await pm.vault.get(VAULT.refreshToken);
    if (!accessToken || !clientSecret || !refreshToken) {
        throw new Error("No refreshable account session exists in Postman Local Vault. Run Login with account first.");
    }
    setAuthRequestVariables({
        accessToken,
        apiSecret: generateApiSecret(await getServerTime(), clientSecret),
        clientSecret,
        clientUniqueId: await getClientUniqueId(),
        refreshToken,
    });
}

async function loadStoredSession() {
    const accessToken = await pm.vault.get(VAULT.bearerToken);
    const clientSecret = await pm.vault.get(VAULT.clientSecret);
    if (accessToken && clientSecret) {
        setAuthRequestVariables({accessToken, clientSecret});
        return;
    }
    if (!pm.variables.get("bearerToken") || !pm.variables.get("clientSecret")) {
        throw new Error("No Postman session found. Run Get anonymous bearer token or Login with account first.");
    }
}

pm.request.headers.upsert({key: "User-Agent", value: USER_AGENT});

if (pm.info.requestName === AUTH_ANONYMOUS) {
    await prepareAnonymousRequest();
} else if (pm.info.requestName === AUTH_LOGIN) {
    await prepareLoginRequest();
} else if (pm.info.requestName === AUTH_REFRESH) {
    await prepareRefreshRequest();
} else if (pm.info.requestName !== "Get server timestamp") {
    await loadStoredSession();
}
