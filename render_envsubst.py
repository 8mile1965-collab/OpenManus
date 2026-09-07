#!/usr/bin/env python3
"""
Render entrypoint shim.

Reads OPENMANU_* environment variables (set in Render dashboard) and
materialises them into config/config.toml before the FastAPI server boots.
This keeps secrets out of git while still using OpenManus's TOML loader.

Run order: render_entrypoint.sh → python render_envsubst.py → uvicorn ...
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "config.toml"


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val if val is not None else default


def main() -> int:
    if not CONFIG_PATH.exists():
        print(f"[envsubst] {CONFIG_PATH} not found, skipping", file=sys.stderr)
        return 0

    raw = CONFIG_PATH.read_text(encoding="utf-8")

    api_key = _env("OPENMANU_LLM_API_KEY")
    if not api_key:
        print(
            "[envsubst] WARNING: OPENMANU_LLM_API_KEY is not set; "
            "LLM calls will fail.",
            file=sys.stderr,
        )
    else:
        # Replace all REPLACE_AT_RUNTIME_FROM_ENV placeholders (config.toml shipped in repo).
        raw = raw.replace("REPLACE_AT_RUNTIME_FROM_ENV", api_key)
        # Also replace YOUR_API_KEY placeholders (config.example.toml when config.toml is absent).
        raw = raw.replace("YOUR_API_KEY", api_key)
        # Override any explicit top-level settings from env.
        raw = re.sub(
            r'(?<=model = ")[^"]+(?=")',
            _env("OPENMANU_LLM_MODEL", "minimax-m3"),
            raw,
            count=1,
        )
        raw = re.sub(
            r'(?<=base_url = ")[^"]+(?=")',
            _env("OPENMANU_LLM_BASE_URL", "https://api.cometapi.com/v1"),
            raw,
            count=1,
        )

    # Ensure [daytona] section exists — DaytonaSettings requires daytona_api_key.
    # Render free tier doesn't use Daytona; an empty placeholder is enough.
    if "[daytona]" not in raw:
        daytona_block = (
            "\n[daytona]\n"
            'daytona_api_key = ""\n'
            'daytona_server_url = "https://app.daytona.io/api"\n'
            'daytona_target = "us"\n'
        )
        raw = raw.rstrip() + "\n" + daytona_block

    CONFIG_PATH.write_text(raw, encoding="utf-8")
    print(f"[envsubst] wrote {CONFIG_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
