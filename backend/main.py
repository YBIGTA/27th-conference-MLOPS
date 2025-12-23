import asyncio
import json
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror
from urllib import parse, request as urlrequest

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="Gap Fill Backend", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_ANON_KEY")
SUPABASE_ALLOW_SUB = os.getenv("SUPABASE_ALLOW_SUB")  # optional allowlist for dev

BASIS_SYMBOL = os.getenv("BINANCE_SYMBOL", "BTCUSDT")
BASE_CANDLE_SECONDS = 15
GAP_STREAM_MAX_MINUTES = int(os.getenv("GAP_STREAM_MAX_MINUTES", "15"))
GAP_STREAM_SLEEP_SECONDS = float(os.getenv("GAP_STREAM_SLEEP_SECONDS", "1.5"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", str(30 * 60)))
BOOTSTRAP_PAGE_LIMIT = int(os.getenv("BOOTSTRAP_PAGE_LIMIT", "5000"))
RING_BUFFER_MINUTES = int(os.getenv("RING_BUFFER_MINUTES", "30"))
PRICE_BUFFER = deque(maxlen=int((RING_BUFFER_MINUTES * 60) / BASE_CANDLE_SECONDS) + 10)


@dataclass
class SessionState:
    session_id: str
    token: str
    supabase_last_ts: Optional[datetime]
    created_at: float
    from_ts: Optional[datetime] = None
    last_emitted_bucket: Optional[int] = None
    stop_event: asyncio.Event = asyncio.Event()


SESSIONS: Dict[str, SessionState] = {}


def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, sess in SESSIONS.items() if now - sess.created_at > SESSION_TTL_SECONDS]
    for sid in expired:
        SESSIONS.pop(sid, None)


def require_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing.")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Use 'Authorization: Bearer <token>'.")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token is empty.")
    return token


def ensure_supabase_config():
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise HTTPException(status_code=500, detail="SUPABASE_URL and SUPABASE_API_KEY/ANON_KEY are required.")


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def supabase_headers(token: str) -> Dict[str, str]:
    ensure_supabase_config()
    return {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {token}",
    }


async def verify_supabase_user(token: str) -> Dict[str, Any]:
    ensure_supabase_config()
    url = f"{SUPABASE_URL}/auth/v1/user"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, headers=supabase_headers(token))
    if resp.status_code == 200:
        payload = resp.json()
        if SUPABASE_ALLOW_SUB and payload.get("sub") != SUPABASE_ALLOW_SUB:
            raise HTTPException(status_code=403, detail="Supabase user not allowed.")
        return payload
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Supabase token invalid.")
    raise HTTPException(status_code=502, detail=f"Supabase auth error {resp.status_code}")


async def fetch_supabase_last_ts(token: str) -> Optional[datetime]:
    ensure_supabase_config()
    url = f"{SUPABASE_URL}/rest/v1/price_15s"
    params = {"select": "ts", "order": "ts.desc", "limit": 1}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=supabase_headers(token))
    if resp.status_code == 200:
        rows = resp.json()
        if not rows:
            return None
        return parse_ts(rows[0].get("ts"))
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Supabase token rejected while reading price_15s.")
    raise HTTPException(status_code=502, detail=f"Supabase REST error {resp.status_code}")


async def fetch_supabase_page(
    token: str,
    cursor: Optional[str] = None,
    limit: int = BOOTSTRAP_PAGE_LIMIT,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
) -> dict:
    ensure_supabase_config()
    limit = max(1, min(limit, BOOTSTRAP_PAGE_LIMIT))
    url = f"{SUPABASE_URL}/rest/v1/price_15s"
    params: Dict[str, str] = {
        "select": "ts,open,high,low,close,volume",
        "order": "ts.desc",
        "limit": str(limit),
    }
    if cursor:
        params["ts"] = f"lt.{cursor}"
    if from_ts:
        params["ts"] = f"gte.{from_ts}"
    if to_ts:
        params["ts"] = f"lte.{to_ts}"

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, params=params, headers=supabase_headers(token))
    if resp.status_code == 200:
        rows = resp.json()
        items = []
        for row in rows:
            ts = parse_ts(row.get("ts"))
            if not ts:
                continue
            items.append(
                {
                    "ts": to_iso(ts),
                    "open": float(row.get("open")) if row.get("open") is not None else None,
                    "high": float(row.get("high")) if row.get("high") is not None else None,
                    "low": float(row.get("low")) if row.get("low") is not None else None,
                    "close": float(row.get("close")) if row.get("close") is not None else None,
                    "volume": float(row.get("volume")) if row.get("volume") is not None else None,
                }
            )
        next_cursor = items[-1]["ts"] if len(items) == limit else None
        return {"items": items, "next_cursor": next_cursor, "has_more": bool(next_cursor)}
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Supabase token rejected while paging price_15s.")
    raise HTTPException(status_code=502, detail=f"Supabase REST error {resp.status_code}")


def resolve_session_and_token(session_id: Optional[str], authorization: Optional[str]) -> Tuple[Optional[SessionState], str]:
    token_from_header = require_token(authorization) if authorization else None
    if session_id:
        sess = SESSIONS.get(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="Session not found or expired.")
        if token_from_header and token_from_header != sess.token:
            raise HTTPException(status_code=401, detail="Bearer token does not match session.")
        return sess, sess.token
    if token_from_header:
        return None, token_from_header
    raise HTTPException(status_code=401, detail="Provide session_id or Authorization header.")


def format_sse(data: Dict[str, Any], event: Optional[str] = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(data)}\n\n"


async def fetch_supabase_news(
    token: str,
    limit: int = 20,
    offset: int = 0,
) -> List[dict]:
    """Fetch news rows from Supabase REST (newest first, optional before cursor for older paging)."""
    ensure_supabase_config()
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    url = f"{SUPABASE_URL}/rest/v1/news"
    params: Dict[str, str] = {
        "select": "id,published_at,title,link,summary",
        "order": "published_at.desc",
    }
    headers = supabase_headers(token)
    headers["Range-Unit"] = "items"
    headers["Range"] = f"{offset}-{offset + limit - 1}"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 200:
        rows = resp.json()
        items = []
        for row in rows:
            ts = parse_ts(row.get("published_at"))
            if not ts:
                continue
            ts_fmt = ts.astimezone(timezone.utc).strftime("%Y/%m/%d/%H/%M")
            items.append(
                {
                    "id": row.get("id"),
                    "published_at": ts_fmt,
                    "published_at_iso": to_iso(ts),
                    "title": row.get("title"),
                    "link": row.get("link"),
                    "summary": row.get("summary"),
                }
            )
        return items
    if resp.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Supabase token rejected while reading news.")
    raise HTTPException(status_code=502, detail=f"Supabase REST error {resp.status_code}")


def fetch_binance_trades(start_time_ms: Optional[int] = None) -> List[dict]:
    base_url = "https://api.binance.com/api/v3/aggTrades"
    params = {"symbol": BASIS_SYMBOL, "limit": 1000}
    if start_time_ms:
        params["startTime"] = start_time_ms
    url = f"{base_url}?{parse.urlencode(params)}"
    req = urlrequest.Request(url, headers={"User-Agent": "gap-fill-backend"})
    with urlrequest.urlopen(req, timeout=10) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=502, detail=f"Binance returned {resp.status}")
        data = resp.read()
        return json.loads(data.decode("utf-8"))


def trades_to_candles(trades: List[dict], bucket_seconds: int = BASE_CANDLE_SECONDS) -> List[dict]:
    bucket_seconds = max(1, bucket_seconds)
    buckets: Dict[int, dict] = {}
    for tr in trades:
        ts_ms = tr.get("T")
        if ts_ms is None:
            continue
        sec = ts_ms // 1000
        price = float(tr["p"])
        qty = float(tr["q"])
        bucket_start = (sec // bucket_seconds) * bucket_seconds
        bucket = buckets.get(bucket_start)
        if not bucket:
            buckets[bucket_start] = {
                "time": bucket_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": qty,
            }
        else:
            bucket["high"] = max(bucket["high"], price)
            bucket["low"] = min(bucket["low"], price)
            bucket["close"] = price
            bucket["volume"] += qty
    return [buckets[k] for k in sorted(buckets.keys())]


def candle_payload(bucket_start: int, candle: dict) -> dict:
    ts_dt = datetime.utcfromtimestamp(bucket_start).replace(tzinfo=timezone.utc)
    return {
        "ts": ts_dt.isoformat(),
        "open": round(float(candle["open"]), 6),
        "high": round(float(candle["high"]), 6),
        "low": round(float(candle["low"]), 6),
        "close": round(float(candle["close"]), 6),
        "volume": round(float(candle.get("volume", 0.0)), 8),
    }


def clamp_gap_end(start_dt: datetime, requested_end: Optional[datetime]) -> datetime:
    max_end = start_dt + timedelta(minutes=GAP_STREAM_MAX_MINUTES)
    if requested_end:
        return min(max_end, requested_end)
    return max_end


async def gap_stream_generator(request: Request, session: SessionState, from_dt: datetime, to_dt: Optional[datetime]):
    hard_end = clamp_gap_end(from_dt, to_dt)
    start_ms = int(from_dt.timestamp() * 1000)
    last_bucket = session.last_emitted_bucket or (int(from_dt.timestamp()) // BASE_CANDLE_SECONDS) * BASE_CANDLE_SECONDS
    while True:
        if session.stop_event.is_set():
            yield format_sse({"message": "session stopped"}, event="close")
            break
        if await request.is_disconnected():
            break
        try:
            trades = await asyncio.to_thread(fetch_binance_trades, start_ms)
            candles = trades_to_candles(trades)
        except Exception as exc:
            yield format_sse({"error": str(exc)}, event="error")
            await asyncio.sleep(GAP_STREAM_SLEEP_SECONDS)
            continue

        emitted = False
        for c in candles:
            bucket_start = c["time"]
            if bucket_start <= (session.last_emitted_bucket or last_bucket):
                continue
            bucket_dt = datetime.utcfromtimestamp(bucket_start).replace(tzinfo=timezone.utc)
            if bucket_dt > hard_end:
                break
            session.last_emitted_bucket = bucket_start
            emitted = True
            payload = candle_payload(bucket_start, c)
            PRICE_BUFFER.append(payload)
            yield format_sse(payload, event="candle")

        if session.last_emitted_bucket:
            bucket_dt = datetime.utcfromtimestamp(session.last_emitted_bucket).replace(tzinfo=timezone.utc)
            if bucket_dt >= hard_end:
                yield format_sse({"message": "gap window complete"}, event="done")
                break

        if not emitted:
            yield f": keepalive {time.time()}\n\n"
        start_ms = (session.last_emitted_bucket or last_bucket) * 1000 + 1
        await asyncio.sleep(GAP_STREAM_SLEEP_SECONDS)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "time": time.time()}


@app.post("/session/start")
async def session_start(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    body: Optional[dict] = Body(default=None),
):
    cleanup_sessions()
    token = require_token(authorization)
    await verify_supabase_user(token)
    from_ts_str = (body or {}).get("from_ts") if body else None
    from_dt = parse_ts(from_ts_str) if from_ts_str else None
    supabase_last_ts = await fetch_supabase_last_ts(token)
    session_id = uuid.uuid4().hex
    session = SessionState(
        session_id=session_id,
        token=token,
        supabase_last_ts=supabase_last_ts,
        created_at=time.time(),
        from_ts=from_dt,
        stop_event=asyncio.Event(),
    )
    SESSIONS[session_id] = session
    stream_url = f"/stream/gap?session_id={session_id}"
    if supabase_last_ts:
        stream_url += f"&from={to_iso(supabase_last_ts)}"
    bootstrap_url = f"/bootstrap?session_id={session_id}"
    return {
        "session_id": session_id,
        "supabase_last_ts": to_iso(supabase_last_ts),
        "stream_url": stream_url,
        "bootstrap_url": bootstrap_url,
        "page_limit": BOOTSTRAP_PAGE_LIMIT,
        "gap_max_minutes": GAP_STREAM_MAX_MINUTES,
    }


@app.post("/session/stop")
async def session_stop(session_id: str):
    sess = SESSIONS.pop(session_id, None)
    if sess and sess.stop_event:
        sess.stop_event.set()
    return {"stopped": bool(sess)}


@app.get("/bootstrap")
async def bootstrap(
    session_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = BOOTSTRAP_PAGE_LIMIT,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    session, token = resolve_session_and_token(session_id, authorization)
    page = await fetch_supabase_page(token, cursor=cursor, limit=limit, from_ts=from_ts, to_ts=to_ts)
    return page


@app.get("/stream/gap")
async def stream_gap(
    request: Request,
    session_id: str,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    start_dt = parse_ts(from_ts) or session.supabase_last_ts or session.from_ts
    if not start_dt:
        raise HTTPException(status_code=400, detail="from_ts required when session has no supabase_last_ts.")
    end_dt = parse_ts(to_ts) if to_ts else None
    generator = gap_stream_generator(request, session, start_dt, end_dt)
    return StreamingResponse(generator, media_type="text/event-stream")


@app.get("/news")
async def list_news(
    limit: int = 20,
    offset: int = 0,
    authorization: Optional[str] = Header(default=None),
):
    """List latest news rows from Supabase."""
    token = require_token(authorization)
    await verify_supabase_user(token)
    items = await fetch_supabase_news(token, limit=limit, offset=offset)
    return {"items": items}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)
