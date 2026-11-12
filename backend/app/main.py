import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import chats, dashboard, documents, library
from .services.scheduler import run_reminder_check, scheduler

LATENCIES: list[float] = []  # seconds; used for the API p95 metric


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    scheduler.add_job(run_reminder_check, "cron", hour=settings.reminder_hour, minute=0,
                      id="daily-reminders", replace_existing=True)
    scheduler.start()
    catch_up = asyncio.create_task(run_reminder_check())  # fire reminders missed while the app was off
    yield
    catch_up.cancel()
    scheduler.shutdown(wait=False)


app = FastAPI(title="Obligo — Contract Obligation Tracker", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path.startswith("/api") and not request.url.path.endswith(("/ask", "/upload", "/compare")):
        LATENCIES.append(time.perf_counter() - start)
        del LATENCIES[:-1000]
    response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}"
    return response


@app.get("/api/metrics")
def metrics():
    xs = sorted(LATENCIES)
    p95 = xs[int(0.95 * (len(xs) - 1))] * 1000 if xs else 0
    return {"requests": len(xs), "api_p95_ms": round(p95, 1)}


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(documents.router)
app.include_router(chats.router)
app.include_router(dashboard.router)
app.include_router(library.router)