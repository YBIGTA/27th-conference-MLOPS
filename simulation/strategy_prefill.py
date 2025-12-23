import argparse
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv
import httpx
import statistics

# Strategy definitions (mirrors frontend/engine/main.py)
STRATEGY_DEFS = [
    {"key": "trend", "timeframe": 60},
    {"key": "mean_revert", "timeframe": 30},
    {"key": "breakout", "timeframe": 15},
    {"key": "scalper", "timeframe": 15},
    {"key": "long_hold", "timeframe": 15},
    {"key": "short_hold", "timeframe": 15},
]
POSITION_NOTIONAL = 10.0
FEE_RATE = 0.0004
BASE_CANDLE_SECONDS = 15
PRICE_WINDOW_ROWS = 40
PAGE_SIZE = 5000

DEFAULT_CSV_LOG = Path(__file__).resolve().parent / "simulations_10m.csv"


def load_env_file(path: Path) -> bool:
    if not path.exists():
        return False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def preload_env():
    here_env = Path(__file__).resolve().parent / ".env"
    cwd_env = Path.cwd() / ".env"
    load_env_file(here_env)
    if cwd_env != here_env:
        load_env_file(cwd_env)


preload_env()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_API_KEY")


def require_env():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_ROLE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_API_KEY)")
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")


def to_utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def isoformat(dt: datetime) -> str:
    return to_utc(dt).replace(microsecond=0).isoformat()


def truncate_to_10m(dt: datetime) -> datetime:
    dt = to_utc(dt)
    minute = (dt.minute // 10) * 10
    return dt.replace(minute=minute, second=0, microsecond=0)


def supabase_headers() -> Dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_bar(self) -> dict:
        return {
            "time": int(self.ts.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def safe_pct_change(current: float, previous: float) -> float:
    if previous is None or previous == 0:
        return 0.0
    return (current - previous) / previous


def compute_rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains = 0.0
    losses = 0.0
    start = len(values) - period
    for idx in range(start, len(values)):
        delta = values[idx] - values[idx - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def aggregate_candles_to_timeframe(candles: List[dict], timeframe_seconds: int) -> List[dict]:
    if timeframe_seconds <= 1:
        return list(candles)
    buckets = {}
    for c in candles:
        ts = int(c.get("time") or 0)
        bucket = (ts // timeframe_seconds) * timeframe_seconds
        existing = buckets.get(bucket)
        if not existing:
            buckets[bucket] = {
                "time": bucket,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0),
            }
        else:
            existing["high"] = max(existing["high"], c["high"])
            existing["low"] = min(existing["low"], c["low"])
            existing["close"] = c["close"]
            existing["volume"] = existing.get("volume", 0) + c.get("volume", 0)
    return [buckets[k] for k in sorted(buckets.keys())]


def strategy_signal(
    strategy_key: str,
    last_close: float,
    fast_ma: Optional[float],
    slow_ma: Optional[float],
    rsi_val: Optional[float],
    range_pos: float,
    high_50: float,
    low_50: float,
    mom_15: float,
    vol_pct: float,
) -> Optional[str]:
    if strategy_key == "trend":
        if fast_ma and slow_ma:
            if fast_ma > slow_ma * 1.001 and mom_15 >= 0:
                return "long"
            if fast_ma < slow_ma * 0.999 and mom_15 <= 0:
                return "short"
    elif strategy_key == "mean_revert":
        if rsi_val is not None:
            if rsi_val > 65:
                return "short"
            if rsi_val < 35:
                return "long"
    elif strategy_key == "breakout":
        if last_close >= high_50 * 0.999:
            return "long"
        if last_close <= low_50 * 1.001:
            return "short"
        if abs(mom_15) > 0.004 and vol_pct > 0.003:
            return "long" if mom_15 > 0 else "short"
    elif strategy_key == "scalper":
        if vol_pct < 0.0015:
            return None
        if 0.35 <= range_pos <= 0.65:
            if mom_15 > 0:
                return "long"
            if mom_15 < 0:
                return "short"
    elif strategy_key == "long_hold":
        return "long"
    elif strategy_key == "short_hold":
        return "short"
    return None


def simulate_strategy_performance(candles_by_key: dict) -> dict:
    def default_perf():
        return {
            "return_pct": 0.0,
            "total_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "trade_count": 0,
            "open_side": None,
            "fees_paid": 0.0,
        }

    perf = {}
    for cfg in STRATEGY_DEFS:
        key = cfg["key"]
        series = candles_by_key.get(key, [])
        closes = [c["close"] for c in series if c.get("close") is not None]
        if len(closes) < 20:
            perf[key] = default_perf()
            continue
        closes = closes[-600:]
        side = None
        entry_price = None
        qty = None
        cumulative_pnl = 0.0
        trade_count = 0
        fees_paid = 0.0

        last_price = closes[-1]
        for idx in range(1, len(closes)):
            window = closes[: idx + 1]
            last_close = window[-1]
            fast_ma = sma(window, 20)
            slow_ma = sma(window, 60)
            rsi_val = compute_rsi(window, 14)
            high_50 = max(window[-50:]) if len(window) >= 50 else max(window)
            low_50 = min(window[-50:]) if len(window) >= 50 else min(window)
            if high_50 == low_50:
                range_pos = 0.5
            else:
                range_pos = (last_close - low_50) / (high_50 - low_50)
            range_pos = max(0.0, min(1.0, range_pos))
            mom_15 = safe_pct_change(last_close, window[-15]) if len(window) > 15 else 0.0
            vol_window = window[-60:] if len(window) >= 2 else window
            vol_pct = statistics.pstdev(vol_window) / last_close if len(vol_window) >= 2 and last_close else 0.0

            signal = strategy_signal(
                key,
                last_close,
                fast_ma,
                slow_ma,
                rsi_val,
                range_pos,
                high_50,
                low_50,
                mom_15,
                vol_pct,
            )

            if side and signal != side:
                if entry_price and qty:
                    if side == "long":
                        pnl = (last_close - entry_price) * qty
                    else:
                        pnl = (entry_price - last_close) * qty
                    close_fee = POSITION_NOTIONAL * FEE_RATE
                    pnl -= close_fee
                    cumulative_pnl += pnl
                    trade_count += 1
                    fees_paid += close_fee
                side = None
                entry_price = None
                qty = None

            if signal and side is None:
                qty = POSITION_NOTIONAL / last_close if last_close else 0.0
                side = signal
                entry_price = last_close
                open_fee = POSITION_NOTIONAL * FEE_RATE
                cumulative_pnl -= open_fee
                fees_paid += open_fee

            last_price = last_close

        unreal = 0.0
        if side and entry_price and qty and last_price:
            if side == "long":
                unreal = (last_price - entry_price) * qty
            else:
                unreal = (entry_price - last_price) * qty
        total_pnl = cumulative_pnl + unreal
        return_pct = (total_pnl / POSITION_NOTIONAL * 100) if POSITION_NOTIONAL else 0.0
        perf[key] = {
            "return_pct": round(return_pct, 2),
            "total_pnl": round(total_pnl, 2),
            "unrealized_pnl": round(unreal, 2),
            "trade_count": trade_count,
            "open_side": side,
            "fees_paid": round(fees_paid, 4),
        }
    return perf


def aggregate_for_strategies(candles_15s: List[Candle]) -> Dict[str, List[dict]]:
    base = [c.to_bar() for c in candles_15s]
    return {
        cfg["key"]: aggregate_candles_to_timeframe(base, cfg.get("timeframe", BASE_CANDLE_SECONDS)) for cfg in STRATEGY_DEFS
    }


async def fetch_price_window(client: httpx.AsyncClient, base_ts: datetime) -> List[Candle]:
    url = f"{SUPABASE_URL}/rest/v1/price_15s"
    params = {
        "select": "ts,open,high,low,close,volume",
        "order": "ts.desc",
        "limit": str(PRICE_WINDOW_ROWS),
        "ts": f"lt.{isoformat(base_ts)}",
    }
    resp = await client.get(url, params=params, headers=supabase_headers(), timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    candles = []
    for row in reversed(rows):
        ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
        candles.append(
            Candle(
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            )
        )
    return candles


async def fetch_first_ts(client: httpx.AsyncClient) -> Optional[datetime]:
    url = f"{SUPABASE_URL}/rest/v1/price_15s"
    params = {"select": "ts", "order": "ts.asc", "limit": 1}
    resp = await client.get(url, params=params, headers=supabase_headers(), timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    return datetime.fromisoformat(rows[0]["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)


async def upsert_simulation(client: httpx.AsyncClient, base_ts: datetime, returns: Dict[str, float]):
    url = f"{SUPABASE_URL}/rest/v1/simulations_10m?on_conflict=ts"
    payload = [
        {
            "ts": isoformat(base_ts),
            "trend_return_pct": returns.get("trend", 0.0),
            "mean_revert_return_pct": returns.get("mean_revert", 0.0),
            "breakout_return_pct": returns.get("breakout", 0.0),
            "scalper_return_pct": returns.get("scalper", 0.0),
            "long_hold_return_pct": returns.get("long_hold", 0.0),
            "short_hold_return_pct": returns.get("short_hold", 0.0),
        }
    ]
    headers = supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    resp = await client.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def ensure_csv(path: Path, fieldnames: List[str]):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()


def append_csv(path: Path, fieldnames: List[str], row: Dict[str, Any]):
    ensure_csv(path, fieldnames)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow({k: row.get(k, "") for k in fieldnames})


async def process_base_ts(
    base_ts: datetime,
    client: httpx.AsyncClient,
    min_price_rows: int = PRICE_WINDOW_ROWS,
) -> Dict[str, Any]:
    window_end = truncate_to_10m(base_ts)
    window_start = window_end - timedelta(minutes=10)

    price_window_raw = await fetch_price_window(client, window_end)
    price_window = [c for c in price_window_raw if c.ts >= window_start]
    if len(price_window) < min_price_rows:
        raw_min = price_window_raw[0].ts if price_window_raw else None
        raw_max = price_window_raw[-1].ts if price_window_raw else None
        if not price_window:
            print(
                f"[skip] {isoformat(window_end)} no price rows "
                f"(raw_count={len(price_window_raw)}, raw_range=[{isoformat(raw_min) if raw_min else 'n/a'} .. {isoformat(raw_max) if raw_max else 'n/a'}], "
                f"window=[{isoformat(window_start)} .. {isoformat(window_end)}])"
            )
            return {
                "ts": isoformat(window_end),
                "status": "skip",
                "reason": "no_price_rows",
                "trend_return_pct": "",
                "mean_revert_return_pct": "",
                "breakout_return_pct": "",
                "scalper_return_pct": "",
                "long_hold_return_pct": "",
                "short_hold_return_pct": "",
            }
        else:
            print(
                f"[warn] {isoformat(window_end)} proceeding with partial price rows "
                f"({len(price_window)}/{min_price_rows} required). raw_count={len(price_window_raw)}, "
                f"raw_range=[{isoformat(raw_min) if raw_min else 'n/a'} .. {isoformat(raw_max) if raw_max else 'n/a'}]"
            )

    aggregated = aggregate_for_strategies(price_window)
    perf = simulate_strategy_performance(aggregated)
    returns = {k: perf.get(k, {}).get("return_pct", 0.0) for k in [cfg["key"] for cfg in STRATEGY_DEFS]}
    if "long_hold" in returns:
        returns["short_hold"] = -returns.get("long_hold", 0.0)

    await upsert_simulation(client, window_end, returns)
    print(f"[ok] simulations_10m upserted for ts={isoformat(window_end)}")
    return {
        "ts": isoformat(window_end),
        "status": "ok",
        "reason": "",
        "trend_return_pct": returns.get("trend", 0.0),
        "mean_revert_return_pct": returns.get("mean_revert", 0.0),
        "breakout_return_pct": returns.get("breakout", 0.0),
        "scalper_return_pct": returns.get("scalper", 0.0),
        "long_hold_return_pct": returns.get("long_hold", 0.0),
        "short_hold_return_pct": returns.get("short_hold", 0.0),
    }


async def main(args):
    require_env()
    csv_path = Path(args.csv_path).expanduser().resolve() if args.csv_path else None
    csv_fields = [
        "ts",
        "status",
        "reason",
        "trend_return_pct",
        "mean_revert_return_pct",
        "breakout_return_pct",
        "scalper_return_pct",
        "long_hold_return_pct",
        "short_hold_return_pct",
    ]

    async with httpx.AsyncClient() as client:
        now_ts = truncate_to_10m(datetime.now(timezone.utc))
        end = truncate_to_10m(args.to_ts) if args.to_ts else now_ts
        if args.from_ts:
            start = truncate_to_10m(args.from_ts)
        else:
            last_sim = await fetch_last_sim_ts(client)
            if last_sim:
                start = truncate_to_10m(last_sim + timedelta(minutes=10))
            else:
                first_ts = await fetch_first_ts(client)
                if not first_ts:
                    raise RuntimeError("price_15s has no data")
                start = truncate_to_10m(first_ts + timedelta(minutes=10))
        if start > end:
            print(f"[info] start {isoformat(start)} is after end {isoformat(end)}, nothing to do.")
            return
        for base_ts in iter_base_times(start, end):
            try:
                row = await process_base_ts(base_ts, client, min_price_rows=args.min_price_rows)
            except Exception as exc:
                print(f"[error] ts={isoformat(base_ts)}: {exc}")
                row = {
                    "ts": isoformat(truncate_to_10m(base_ts)),
                    "status": "error",
                    "reason": str(exc),
                    "trend_return_pct": "",
                    "mean_revert_return_pct": "",
                    "breakout_return_pct": "",
                    "scalper_return_pct": "",
                    "long_hold_return_pct": "",
                    "short_hold_return_pct": "",
                }
            if csv_path:
                append_csv(csv_path, csv_fields, row)
            if args.sleep_seconds:
                await asyncio.sleep(args.sleep_seconds)


def iter_base_times(start: datetime, end: datetime):
    current = truncate_to_10m(start)
    end = truncate_to_10m(end)
    while current <= end:
        yield current
        current += timedelta(minutes=10)


async def fetch_last_sim_ts(client: httpx.AsyncClient) -> Optional[datetime]:
    """Most recent ts from simulations_10m."""
    url = f"{SUPABASE_URL}/rest/v1/simulations_10m"
    params = {"select": "ts", "order": "ts.desc", "limit": "1"}
    resp = await client.get(url, params=params, headers=supabase_headers(), timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    ts_raw = rows[0].get("ts")
    if not ts_raw:
        return None
    return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prefill simulations_10m via Supabase REST using strategy returns.")
    parser.add_argument("--from-ts", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), help="Start ts (inclusive) aligned to 10m, UTC ISO.")
    parser.add_argument("--to-ts", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), help="End ts (inclusive) aligned to 10m, UTC ISO. Default: now.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep between windows.")
    parser.add_argument("--min-price-rows", type=int, default=PRICE_WINDOW_ROWS, help="Minimum 15s rows required for a 10m window.")
    parser.add_argument("--csv-path", type=str, default=str(DEFAULT_CSV_LOG), help="CSV log path (default simulations_10m.csv). Set empty string to disable.")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
