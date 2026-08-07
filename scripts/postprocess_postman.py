"""Normalize the generated Postman collection for safe, stable publication."""

from __future__ import annotations

import json
import re
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

COLLECTION_ID = str(
    uuid.uuid5(
        uuid.NAMESPACE_URL,
        "https://github.com/agmmnn/eksiapi#postman-collection",
    )
)
UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
PLACEHOLDER_UUID = "00000000-0000-4000-8000-000000000000"
ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIRECTORY = ROOT / "postman" / "scripts"
STABLE_INTEGER_FIELDS = {
    "ArchiveId",
    "EntryId",
    "Id",
    "MaxMessageId",
    "Owner",
    "Rate",
    "ThreadId",
    "TopicId",
}


def remove_generated_ids(value: Any) -> None:
    if isinstance(value, dict):
        generated_id = value.get("id")
        if isinstance(generated_id, str):
            try:
                uuid.UUID(generated_id)
            except ValueError:
                pass
            else:
                del value["id"]
        for child in value.values():
            remove_generated_ids(child)
    elif isinstance(value, list):
        for child in value:
            remove_generated_ids(child)


def normalize_generated_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_generated_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [normalize_generated_values(child) for child in value]
    if isinstance(value, str):
        return UUID_PATTERN.sub(PLACEHOLDER_UUID, value)
    return value


def normalize_responses(value: Any) -> None:
    if isinstance(value, dict):
        responses = value.get("response")
        if isinstance(responses, list):
            for response in responses:
                code = response.get("code")
                for header in response.get("header", []):
                    if header.get("key", "").lower() == "retry-after":
                        header["value"] = "60"
                body = response.get("body")
                if not isinstance(body, str):
                    continue
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and isinstance(code, int):
                    if "Success" in payload:
                        payload["Success"] = 200 <= code < 300
                    if "StatusCode" in payload:
                        payload["StatusCode"] = code
                    if payload.get("Message") == "string" and 200 <= code < 300:
                        payload["Message"] = None
                response["body"] = json.dumps(payload, ensure_ascii=False, indent=2)
        for child in value.values():
            normalize_responses(child)
    elif isinstance(value, list):
        for child in value:
            normalize_responses(child)


def normalize_request_payload(value: Any) -> Any:
    if isinstance(value, dict):
        if value and all(key.startswith("key_") for key in value):
            return {"key_0": "example"}
        normalized: dict[str, Any] = {}
        for key, child in value.items():
            if key in STABLE_INTEGER_FIELDS:
                normalized[key] = 1
            elif key.startswith("key_"):
                normalized[key] = "example"
            else:
                normalized[key] = normalize_request_payload(child)
        return normalized
    if isinstance(value, list):
        return [normalize_request_payload(child) for child in value]
    return value


def normalize_request_bodies(value: Any) -> None:
    if isinstance(value, dict):
        body = value.get("body")
        if isinstance(value.get("method"), str) and isinstance(body, dict):
            for field in body.get("urlencoded", []) + body.get("formdata", []):
                if field.get("key") in STABLE_INTEGER_FIELDS:
                    field["value"] = "1"
            raw = body.get("raw")
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    pass
                else:
                    body["raw"] = json.dumps(
                        normalize_request_payload(payload),
                        ensure_ascii=False,
                        indent=2,
                    )
        for child in value.values():
            normalize_request_bodies(child)
    elif isinstance(value, list):
        for child in value:
            normalize_request_bodies(child)


def upsert_variable(collection: dict[str, Any], key: str, value: str) -> None:
    variables = collection.setdefault("variable", [])
    for variable in variables:
        if variable.get("key") == key:
            variable["value"] = value
            variable["type"] = "string"
            return
    variables.append({"key": key, "value": value, "type": "string"})


def script_event(listen: str, filename: str) -> dict[str, Any]:
    script = (SCRIPT_DIRECTORY / filename).read_text(encoding="utf-8")
    return {
        "listen": listen,
        "script": {"type": "text/javascript", "exec": script.splitlines()},
    }


def find_folder(collection: dict[str, Any], name: str) -> dict[str, Any]:
    for folder in collection.get("item", []):
        if folder.get("name") == name:
            return folder
    raise ValueError(f"Postman folder not found: {name}")


def find_request(folder: dict[str, Any], name: str) -> dict[str, Any]:
    for item in folder.get("item", []):
        if item.get("name") == name and "request" in item:
            return item
    raise ValueError(f"Postman request not found: {name}")


def form_field(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": value, "type": "text"}


def auth_form(grant_type: str) -> list[dict[str, Any]]:
    fields = [
        form_field("DeviceModel", "Google sdk_gphone_x86_64"),
        form_field("Platform", "g"),
        form_field("Version", "2.4.10"),
        form_field("Build", "144"),
        form_field("Api-Secret", "{{apiSecret}}"),
        form_field("Client-Secret", "{{clientSecret}}"),
        form_field("ClientUniqueId", "{{clientUniqueId}}"),
        form_field("grant_type", grant_type),
    ]
    if grant_type == "password":
        fields.extend(
            [
                form_field("username", "{{authUsername}}"),
                form_field("password", "{{authPassword}}"),
            ]
        )
    else:
        fields.append(form_field("refresh_token", "{{refreshToken}}"))
    return fields


def sync_original_requests(item: dict[str, Any]) -> None:
    request = item["request"]
    original = {
        "url": deepcopy(request["url"]),
        "header": deepcopy(request.get("header", [])),
        "method": request["method"],
        "body": deepcopy(request.get("body")),
    }
    for response in item.get("response", []):
        response["originalRequest"] = deepcopy(original)


def configure_postman_auth(collection: dict[str, Any]) -> None:
    collection["event"] = [
        script_event("prerequest", "auth-prerequest.js"),
        script_event("test", "auth-postresponse.js"),
    ]
    authentication = find_folder(collection, "Authentication")

    server_time = find_request(authentication, "Get server timestamp")
    server_time["request"]["auth"] = {"type": "noauth"}
    sync_original_requests(server_time)

    anonymous = find_request(authentication, "Get anonymous bearer token")
    anonymous["request"]["auth"] = {"type": "noauth"}
    anonymous["request"]["description"]["content"] += (
        " In Postman, sending this request generates Api-Secret automatically and "
        "stores the resulting session in Local Vault."
    )
    anonymous["request"]["body"]["urlencoded"] = auth_form("anonymous")[:7]
    sync_original_requests(anonymous)

    login = find_request(authentication, "Login with credentials")
    login["name"] = "Login with account"
    login["request"]["name"] = "Login with account"
    login["request"]["auth"] = {
        "type": "bearer",
        "bearer": [{"key": "token", "value": "{{bearerToken}}", "type": "string"}],
    }
    login["request"]["description"]["content"] = (
        "Logs in with eksi-username and eksi-password from Postman Local Vault. "
        "The pre-request script obtains an anonymous token and generates Api-Secret; "
        "the account session is written back to Local Vault."
    )
    login["request"]["body"]["urlencoded"] = auth_form("password")
    sync_original_requests(login)

    refresh = deepcopy(login)
    refresh["name"] = "Refresh session"
    refresh["request"]["name"] = "Refresh session"
    refresh["request"]["description"]["content"] = (
        "Refreshes the account session stored in Postman Local Vault without reading "
        "the account password."
    )
    refresh["request"]["body"]["urlencoded"] = auth_form("refresh_token")
    sync_original_requests(refresh)
    login_index = authentication["item"].index(login)
    authentication["item"].insert(login_index + 1, refresh)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: postprocess_postman.py COLLECTION.json")

    path = Path(sys.argv[1])
    collection = json.loads(path.read_text(encoding="utf-8"))
    remove_generated_ids(collection)
    collection = normalize_generated_values(collection)
    normalize_request_bodies(collection)
    normalize_responses(collection)
    collection["info"]["_postman_id"] = COLLECTION_ID
    upsert_variable(collection, "bearerToken", "")
    upsert_variable(collection, "clientSecret", "")
    upsert_variable(collection, "refreshToken", "")
    configure_postman_auth(collection)
    path.write_text(
        json.dumps(collection, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
