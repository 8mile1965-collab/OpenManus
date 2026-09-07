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
        # We approximate a result by capturing the last assistant message via
        # agent.memory if exposed; otherwise we just return success.
        await agent.run(req.prompt)

        last_msg: Optional[str] = None
        try:
            memory = getattr(agent, "memory", None)
            if memory is not None and getattr(memory, "messages", None):
                last_msg = memory.messages[-1].content
        except Exception:  # pragma: no cover - best effort
            pass

        return {
            "request_id": request_id,
            "status": "completed",
            "prompt": req.prompt,
            "result": last_msg,
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
