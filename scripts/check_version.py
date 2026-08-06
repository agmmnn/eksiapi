"""Fail when a release tag and the package version do not match."""

from __future__ import annotations

import argparse
from importlib.metadata import version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", help="Release tag, for example v1.2.3")
    args = parser.parse_args()

    package_version = version("eksiapi")
    expected_tag = f"v{package_version}"
    if args.tag != expected_tag:
        parser.error(
            f"release tag {args.tag!r} does not match project version "
            f"{package_version!r}; "
            f"expected {expected_tag!r}"
        )
    print(f"Release tag {args.tag} matches eksiapi {package_version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
