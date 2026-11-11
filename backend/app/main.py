"""FastAPI application (Agent A).

Startup: Alembic `upgrade head` (or `stamp head` for a DB created by create_all in tests) →
seed provisioned staff if none exist → demo background episodes (demo mode) → bind SSE bus →
load the persisted simulated clock → recover + start the worker (Agent B) if WORKER_ENABLED.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select

from . import db, errors
from .clock import clock
from .config import BACKEND_DIR, REPO_DIR, settings
from .events import bus
from .models import Role, User

log = logging.getLogger("ninety_six")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "ninety-six"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# --------------------------------------------------------------------------- database bootstrap
def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["configure_logger"] = False
    with db.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        tables = set(inspect(conn).get_table_names())
        if tables and "alembic_version" not in tables:
            command.stamp(cfg, "head")  # schema created by create_all (tests)
        else:
            command.upgrade(cfg, "head")


def seed_if_empty() -> None:
    from .seed_accounts import seed_staff

    with db.session_scope() as s:
        has_staff = s.scalar(select(User.id).where(User.role.in_(Role.STAFF)).limit(1)) is not None
        if not has_staff:
            created = seed_staff(s)
            log.info("Seeded %s staff accounts", created)
        else:
            return
    if settings.demo_mode and os.getenv("NS_SEED_BACKGROUND", "true").lower() not in ("0", "false", "no"):
        try:
            from .services import agent_ops

            with db.session_scope() as s:
                agent_ops.seed_background_episodes(s)
        except NotImplementedError:
            log.info("Background demo episodes not available yet (agent services pending)")
        except Exception:
            log.exception("Could not seed background demo episodes")


def init_database() -> None:
    run_migrations()
    seed_if_empty()
    with db.session_scope() as s:
        clock.load(s)


# --------------------------------------------------------------------------- worker (Agent B)
def _start_worker() -> None:
    if not settings.worker_enabled:
        return
    try:
        from .execution import worker

        try:
            worker.recover_on_startup()
        except Exception:
            log.exception("Worker restart recovery failed")
        worker.start_background_worker()
    except ImportError:
        log.warning("Worker module not available yet; scheduled tasks will not run")
    except Exception:
        log.exception("Could not start worker")


def _stop_worker() -> None:
    try:
        from .execution import worker

        worker.stop_background_worker()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    bus.bind(asyncio.get_running_loop())
    _start_worker()
    yield
    _stop_worker()


app = FastAPI(title="Ninety-Six API", version="0.2.0", lifespan=lifespan)
errors.install_error_handlers(app)


@app.exception_handler(NotImplementedError)
async def _not_implemented(_req: Request, exc: NotImplementedError):  # noqa: ANN202
    return JSONResponse(
        {"error": {"code": "not_implemented", "message": "This feature is not available yet.", "details": {}}},
        status_code=501,
    )


@app.middleware("http")
async def csrf_guard(request: Request, call_next):  # noqa: ANN001, ANN201
    """Non-GET API calls must carry `X-Requested-With: ninety-six`. Cross-site forms can't set it,
    and cross-origin scripts can't without a CORS preflight that only FRONTEND_ORIGIN passes."""
    if request.method not in SAFE_METHODS and request.url.path.startswith("/api/"):
        if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
            return JSONResponse(
                {"error": {"code": "forbidden", "message": "Missing request header.", "details": {"header": "X-Requested-With"}}},
                status_code=403,
            )
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With", "Last-Event-ID"],
)

from .api import auth as auth_routes  # noqa: E402
from .api import demo as demo_routes  # noqa: E402
from .api import episodes as episode_routes  # noqa: E402
from .api import patient as patient_routes  # noqa: E402
from .api import staff as staff_routes  # noqa: E402
from .api import stream as stream_routes  # noqa: E402
from .api import workflow as workflow_routes  # noqa: E402

for r in (auth_routes, staff_routes, episode_routes, workflow_routes, patient_routes, demo_routes, stream_routes):
    app.include_router(r.router)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def api_not_found(rest: str):  # noqa: ARG001
    return JSONResponse({"error": {"code": "not_found", "message": "Not found.", "details": {}}}, status_code=404)


# --------------------------------------------------------------------------- built frontend (optional)
DIST = REPO_DIR / "frontend" / "dist"
if DIST.is_dir():
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
