"""Produce a deterministic Retrofit endpoint inventory from an Ekşi APK.

The script is intentionally read-only: it prints JSON to stdout so callers can
review or redirect it themselves. It requires ``jadx`` on PATH.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HTTP_ANNOTATIONS = {
    "InterfaceC3267zw": "GET",
    "InterfaceC2918uO": "POST",
}
INTERFACE_NAME = "InterfaceC1999fn.java"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_interface(source: str) -> list[dict[str, object]]:
    endpoints: list[dict[str, object]] = []
    annotation = "|".join(HTTP_ANNOTATIONS)
    pattern = re.compile(
        rf'@(?P<annotation>{annotation})\("(?P<path>[^\"]+)"\)'
        r"(?P<declaration>.*?\b(?P<method>m\d+[A-Za-z0-9]*)\(.*?\);)",
        re.DOTALL,
    )
    for match in pattern.finditer(source):
        declaration = match.group("declaration")
        fields = re.findall(
            r'@(?:InterfaceC0368Fq|InterfaceC0909OR)\("([^\"]+)"\)', declaration
        )
        prefix = source[max(0, match.start() - 100) : match.start()]
        endpoints.append(
            {
                "http_method": HTTP_ANNOTATIONS[match.group("annotation")],
                "path": "/" + match.group("path").strip().lstrip("/"),
                "java_method": match.group("method"),
                "fields": fields,
                "form_urlencoded": "@InterfaceC0370Fs" in prefix,
            }
        )
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument(
        "--jadx-dir",
        type=Path,
        help="Use an existing JADX output directory instead of decompiling again",
    )
    args = parser.parse_args()
    apk = args.apk.resolve()
    if not apk.is_file():
        parser.error(f"APK does not exist: {apk}")
    if shutil.which("jadx") is None and args.jadx_dir is None:
        parser.error("jadx is required on PATH")

    with tempfile.TemporaryDirectory(prefix="eksiapi-jadx-") as temporary:
        output = args.jadx_dir or Path(temporary)
        if args.jadx_dir is None:
            subprocess.run(
                ["jadx", "--show-bad-code", "-d", str(output), str(apk)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        candidates = list(output.rglob(INTERFACE_NAME))
        if not candidates:
            raise SystemExit(f"Retrofit interface {INTERFACE_NAME} was not found")
        source = candidates[0].read_text(encoding="utf-8", errors="replace")
        result = {
            "apk": apk.name,
            "sha256": sha256(apk),
            "jadx_version": subprocess.run(
                ["jadx", "--version"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "retrofit_interface": str(candidates[0].relative_to(output)),
            "endpoint_count": len(parse_interface(source)),
            "endpoints": parse_interface(source),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
