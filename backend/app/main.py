import asyncio
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .database import init_db
from .routers import chats, dashboard, documents, library, notes, security
from .services import audit, crypto
from .services.scheduler import run_reminder_check, scheduler

LATENCIES: list[float] = []  # seconds; used for the API p95 metric
OPEN_PATHS = {"/api/health"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    crypto.clean_tmp()          # no plaintext scratch files survive a restart
    crypto.master_key()         # fail fast on a bad key, and create the key file on first run
    scheduler.add_job(run_reminder_check, "cron", hour=settings.reminder_hour, minute=0,
                      id="daily-reminders", replace_existing=True)
    scheduler.start()
    catch_up = asyncio.create_task(run_reminder_check())  # fire reminders missed while the app was off
    yield
    catch_up.cancel()
    scheduler.shutdown(wait=False)


app = FastAPI(title="Obligo — Contract Obligation Tracker", lifespan=lifespan)


# Middleware order: the last one added is the outermost. CORS is added last so even 401 responses carry CORS headers.
@app.middleware("http")
async def timing_and_headers(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api") and not path.endswith(("/ask", "/upload", "/compare")):
        LATENCIES.append(time.perf_counter() - start)
        del LATENCIES[:-1000]
    response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"   # decrypted documents must not linger in caches
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


@app.middleware("http")
async def require_token(request: Request, call_next):
    token = settings.obligo_api_token
    path = request.url.path
    if token and path.startswith("/api") and path not in OPEN_PATHS and request.method != "OPTIONS":
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            audit.log("auth.denied", path, "", request, actor="anonymous")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware, allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Obligo-Kind", "X-Obligo-Filename", "Content-Disposition"],
)


@app.get("/api/metrics")
def metrics():
    xs = sorted(LATENCIES)
    p95 = xs[int(0.95 * (len(xs) - 1))] * 1000 if xs else 0
    return {"requests": len(xs), "api_p95_ms": round(p95, 1)}


@app.get("/api/health")
def health():
    return {"status": "ok"}


for r in (documents, chats, dashboard, library, notes, security):
    app.include_router(r.router)