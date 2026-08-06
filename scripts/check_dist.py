"""Install the built wheel into clean base and MCP environments."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def venv_python(environment: pathlib.Path) -> pathlib.Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def console_script(environment: pathlib.Path, name: str) -> pathlib.Path:
    if os.name == "nt":
        return environment / "Scripts" / f"{name}.exe"
    return environment / "bin" / name


def main() -> int:
    wheels = sorted((ROOT / "dist").glob("eksiapi-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one wheel in dist/, found {len(wheels)}")
    wheel = wheels[0].resolve()

    with tempfile.TemporaryDirectory(prefix="eksiapi-dist-") as temporary:
        work = pathlib.Path(temporary)
        base = work / "base"
        mcp = work / "mcp"

        run("uv", "venv", "--python", sys.executable, str(base), cwd=work)
        base_python = venv_python(base)
        run("uv", "pip", "install", "--python", str(base_python), str(wheel), cwd=work)
        run(
            str(base_python),
            "-c",
            (
                "import importlib.util, eksiapi; "
                "assert importlib.util.find_spec('mcp') is None; "
                "assert importlib.util.find_spec('keyring') is None; "
                "print(eksiapi.__version__)"
            ),
            cwd=work,
        )
        missing_extra = subprocess.run(
            (str(console_script(base, "eksi-mcp")),),
            cwd=work,
            check=False,
            text=True,
            capture_output=True,
        )
        if missing_extra.returncode != 2 or "eksiapi[mcp]" not in missing_extra.stderr:
            raise SystemExit("Base eksi-mcp command did not explain the optional extra")

        run("uv", "venv", "--python", sys.executable, str(mcp), cwd=work)
        mcp_python = venv_python(mcp)
        requirement = f"eksiapi[mcp] @ {wheel.as_uri()}"
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(mcp_python),
            requirement,
            cwd=work,
        )
        run(
            str(mcp_python),
            "-c",
            "from eksiapi.mcp.server import mcp; assert mcp.name == 'eksi-sozluk'",
            cwd=work,
        )
        run(str(console_script(mcp, "eksi-auth")), "--help", cwd=work)

    print(f"Clean installation checks passed for {wheel.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
