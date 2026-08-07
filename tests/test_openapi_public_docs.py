from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "openapi.yaml"
POSTMAN = ROOT / "postman" / "eksi-sozluk-api.postman_collection.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
EXPECTED_TAGS = {
    "Authentication",
    "Feeds",
    "Topics",
    "Entries",
    "Profiles",
    "Comments",
    "Relationships",
    "Messages",
    "Drafts",
    "Settings and devices",
    "Notifications",
    "Trash",
}


def operations(spec: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, path_item in spec["paths"].items():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def collection_requests(items: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for item in items:
        if "request" in item:
            yield item
        yield from collection_requests(item.get("item", []))


def collection_path(item: dict[str, Any]) -> str:
    return "/" + "/".join(item["request"]["url"]["path"])


def test_openapi_is_valid_and_ready_for_public_docs() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    validate(spec)

    declared_tags = {tag["name"] for tag in spec["tags"]}
    assert declared_tags == EXPECTED_TAGS
    assert "/v2/user/me" not in spec["paths"]
    assert spec["info"]["title"].endswith("(Unofficial)")
    assert "https://github.com/agmmnn/eksiapi" in spec["info"]["description"]

    all_operations = list(operations(spec))
    operation_ids = [operation["operationId"] for _, _, operation in all_operations]
    assert len(all_operations) == 93
    assert len(operation_ids) == len(set(operation_ids))

    for path, _, operation in all_operations:
        assert re.fullmatch(r"[a-z][A-Za-z0-9]+", operation["operationId"])
        assert operation["description"].strip()
        assert len(operation["tags"]) == 1
        assert operation["tags"][0] in declared_tags
        assert operation["x-eksi-auth-mode"] in {
            "none",
            "authentication-flow",
            "anonymous",
            "account",
        }
        assert "200" in operation["responses"]
        if path != "/v2/clientsettings/time":
            assert any(
                str(code).startswith(("4", "5")) for code in operation["responses"]
            )


def test_generated_postman_collection_matches_openapi() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    collection = json.loads(POSTMAN.read_text(encoding="utf-8"))
    requests = list(collection_requests(collection["item"]))

    assert {folder["name"] for folder in collection["item"]} == EXPECTED_TAGS
    assert len(requests) == len(list(operations(spec))) + 1
    assert all(folder["name"] != "v2" for folder in collection["item"])

    paths = [collection_path(request) for request in requests]
    assert "/v2/user/me" not in paths
    assert "/v2/user/:nick" in paths

    client_secret_paths = [
        collection_path(item)
        for item in requests
        if any(
            header.get("key", "").lower() == "client-secret"
            and header.get("value") == "{{clientSecret}}"
            for header in item["request"].get("header", [])
        )
    ]
    assert len(client_secret_paths) == 93
    assert "/v2/clientsettings/time" not in client_secret_paths

    authentication = next(
        folder for folder in collection["item"] if folder["name"] == "Authentication"
    )
    auth_requests = {item["name"]: item for item in authentication["item"]}
    assert set(auth_requests) == {
        "Get server timestamp",
        "Get anonymous bearer token",
        "Login with account",
        "Refresh session",
    }
    assert "{{apiSecret}}" in json.dumps(auth_requests, ensure_ascii=False)
    assert "{{authPassword}}" in json.dumps(
        auth_requests["Login with account"], ensure_ascii=False
    )
    assert "{{refreshToken}}" in json.dumps(
        auth_requests["Refresh session"], ensure_ascii=False
    )
    assert auth_requests["Get server timestamp"]["request"]["auth"]["type"] == "noauth"
    assert (
        auth_requests["Get anonymous bearer token"]["request"]["auth"]["type"]
        == "noauth"
    )
    assert auth_requests["Login with account"]["request"]["auth"]["type"] == "bearer"
    assert auth_requests["Refresh session"]["request"]["auth"]["type"] == "bearer"

    events = {
        event["listen"]: "\n".join(event["script"]["exec"])
        for event in collection["event"]
    }
    assert set(events) == {"prerequest", "test"}
    assert 'pm.require("npm:node-forge@1.4.0")' in events["prerequest"]
    assert (
        'pm.request.headers.upsert({key: "User-Agent", value: USER_AGENT})'
        in events["prerequest"]
    )
    assert "pm.vault.get" in events["prerequest"]
    assert "pm.vault.set" in events["test"]

    sensitive_keys = {"password", "bearerToken", "refreshToken", "clientSecret"}
    for variable in collection.get("variable", []):
        if variable.get("key") in sensitive_keys:
            assert variable.get("value") in {None, ""}
