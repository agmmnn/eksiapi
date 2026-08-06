from eksiapi.formatting import sanitize_payload, unwrap_response


def test_unwrap_sanitizes_html_credentials_and_adds_entry_url() -> None:
    payload = {
        "Success": True,
        "Data": {
            "EntryId": 42,
            "Content": "hello<br><b>world</b>",
            "access_token": "must-not-leak",
            "ClientSecret": "must-not-leak-either",
        },
    }

    result = unwrap_response(payload)

    assert result["Content"] == "hello\nworld"
    assert "access_token" not in result
    assert "ClientSecret" not in result
    assert result["source_url"] == "https://eksisozluk.com/entry/42"


def test_sanitize_payload_recurses() -> None:
    result = sanitize_payload([{"password": "secret", "value": "safe"}])
    assert result == [{"value": "safe"}]
