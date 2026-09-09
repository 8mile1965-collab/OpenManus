"""
Render Web Service entry point for OpenManus.

Wraps the Manus agent in a minimal FastAPI app so Render can run it as a
Web Service with a public URL.  POST /run executes a single prompt and
streams the agent log + final answer back as JSON.

This file is intentionally additive — main.py / run_mcp_server.py are
untouched, so local CLI usage still works.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repo root is on sys.path so `app.*` imports resolve.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app.agent.manus import Manus  # noqa: E402
from app.logger import logger  # noqa: E402

APP_NAME = "OpenManus-on-Render"
STARTED_AT = time.time()
app = FastAPI(title=APP_NAME, version="0.1.0")


# ----------------------------- request models ----------------------------- #

class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Task for the agent")
    max_steps: Optional[int] = Field(
        None, description="Optional step cap for the agent loop"
    )


# ------------------------------- endpoints ------------------------------- #

@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "service": APP_NAME,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
        "endpoints": ["/healthz", "/run (POST)"],
    }


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/debug/config")
async def debug_config() -> Dict[str, Any]:
    """Debug endpoint: dump envsubst output + relevant env vars."""
    config_path = ROOT / "config" / "config.toml"
    config_text = ""
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8")
    return {
        "config_toml_exists": config_path.exists(),
        "config_toml_size": len(config_text),
        "config_toml_first_500": config_text[:500],
        "api_key_from_env_set": bool(os.environ.get("OPENMANU_LLM_API_KEY")),
        "api_key_preview": (
            (os.environ.get("OPENMANU_LLM_API_KEY") or "")[:6] + "..." +
            (os.environ.get("OPENMANU_LLM_API_KEY") or "")[-4:]
        ) if os.environ.get("OPENMANU_LLM_API_KEY") else "",
        "model_env": os.environ.get("OPENMANU_LLM_MODEL"),
        "base_url_env": os.environ.get("OPENMANU_LLM_BASE_URL"),
    }


# ----------------------------- tinkoff proxy ----------------------------- #
import urllib.request, urllib.error
TINKOFF_BASE = "https://invest-public-api.tinkoff.ru"


@app.post("/proxy")
async def tinkoff_proxy(req: Dict[str, Any]) -> Dict[str, Any]:
    """Forward POST to invest-public-api.tinkoff.ru from Render's EU/US IP."""
    try:
        path = req.get("path", "/")
        headers = req.get("headers") or {}
        body = req.get("body")
        body_bytes = None
        if body is not None:
            import json as _json
            body_bytes = _json.dumps(body).encode("utf-8")
            if "Content-Type" not in headers and "content-type" not in headers:
                headers["Content-Type"] = "application/json"
        r = urllib.request.Request(TINKOFF_BASE + path, data=body_bytes, method="POST", headers=headers)
        # SSL workaround: trust Russian Trusted Sub CA chain (Tinkoff uses it)
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        try:
            _ctx.load_default_certs()
        except Exception:
            pass
        try:
            with urllib.request.urlopen(r, timeout=20, context=_ctx) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read() if hasattr(e, "read") else b""
            code = e.code
        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except Exception:
            data = {"_raw": text[:2000]}
        return {"status": code, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/run")
async def run(req: RunRequest) -> Dict[str, Any]:
    request_id = uuid.uuid4().hex[:12]
    logger.info(f"[render-main] request {request_id} start: {req.prompt[:120]}")

    try:
        agent = await Manus.create()
    except Exception as exc:  # pragma: no cover - surfaces misconfig early
        logger.exception(f"[render-main] agent init failed: {exc}")
        raise HTTPException(status_code=500, detail=f"agent init failed: {exc}") from exc

    try:
        # Manus.run returns nothing useful directly; we rely on logger output.
        await agent.run(req.prompt)

        last_msg: Optional[str] = None
        all_messages: list = []
        try:
            memory = getattr(agent, "memory", None)
            if memory is not None and getattr(memory, "messages", None):
                for m in memory.messages:
                    role = getattr(m, "role", "?")
                    content = getattr(m, "content", "")
                    if isinstance(content, list):
                        # tool calls or multi-block content
                        content = " ".join(
                            str(b.get("text", b)) if isinstance(b, dict) else str(b)
                            for b in content
                        )
                    all_messages.append({"role": role, "content": str(content)[:1500]})
                last_msg = str(memory.messages[-1].content)
                if isinstance(last_msg, list):
                    last_msg = " ".join(
                        str(b.get("text", b)) if isinstance(b, dict) else str(b)
                        for b in last_msg
                    )
        except Exception:  # pragma: no cover - best effort
            pass

        return {
            "request_id": request_id,
            "status": "completed",
            "prompt": req.prompt,
            "result": last_msg,
            "messages": all_messages,
            "step_count": len(all_messages),
        }
    except Exception as exc:
        logger.exception(f"[render-main] request {request_id} failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        try:
            await agent.cleanup()
        except Exception:  # pragma: no cover
            pass


if __name__ == "__main__":  # local debug only
    import uvicorn

    uvicorn.run(
        "render_main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        reload=False,
    )
