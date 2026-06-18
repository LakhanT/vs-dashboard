"""Live prices via Fyers WebSocket stream + debounced watchlist."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Full, Queue

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.db_write import commit_session, run_write_with_retry, serialized_write
from app.models import Stock
from app.services.dashboard_service import invalidate_universe_cache
from app.services.fyers_stream import fyers_stream_service
from app.services.market_data import fetch_live_quotes_for_stocks
from app.services.ranking_engine import patch_live_ltps_only, recalculate_rankings_from_db

logger = logging.getLogger(__name__)
settings = get_settings()

RANK_RECALC_INTERVAL_SEC = 45
LTP_FLUSH_INTERVAL_SEC = 8
POLL_FALLBACK_INTERVAL_SEC = 5
WATCH_DEBOUNCE_SEC = 0.5
MAX_WATCH_SYMBOLS = 100


@dataclass
class LivePriceStatus:
    running: bool = False
    mode: str = "stream"
    interval_sec: int = 3
    watch_count: int = 0
    stream_connected: bool = False
    last_run_at: datetime | None = None
    last_duration_sec: float | None = None
    last_updated: int = 0
    last_error: str | None = None
    last_quote_source: str | None = None
    total_ticks: int = 0
    subscribers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "interval_sec": self.interval_sec,
                "watch_count": self.watch_count,
                "stream_connected": self.stream_connected,
                "batch_size": self.watch_count,
                "universe_count": self.watch_count,
                "cursor": 0,
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "last_duration_sec": self.last_duration_sec,
                "last_updated": self.last_updated,
                "last_error": self.last_error,
                "last_quote_source": self.last_quote_source,
                "total_cycles": self.total_ticks,
                "total_ticks": self.total_ticks,
                "subscribers": self.subscribers,
            }


class LivePriceService:
    def __init__(self) -> None:
        self.status = LivePriceStatus(interval_sec=settings.live_price_interval_sec)
        self._stop_event = threading.Event()
        self._rank_thread: threading.Thread | None = None
        self._poll_thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None
        self._ws_queues: list[Queue] = []
        self._ws_lock = threading.Lock()
        self._watch_lock = threading.Lock()
        self._connection_watches: dict[int, set[str]] = {}
        self._ltp_cache: dict[int, float] = {}
        self._scrip_ltps: dict[str, dict] = {}
        self._scrip_to_live_id: dict[str, int] = {}
        self._ltp_cache_lock = threading.Lock()
        self._watch_debounce_timer: threading.Timer | None = None
        self._last_synced_key: str | None = None

        fyers_stream_service.set_tick_handler(self._on_stream_tick)

    def get_scrip_ltps(self) -> dict[str, dict]:
        with self._ltp_cache_lock:
            return dict(self._scrip_ltps)

    def start(self) -> None:
        with self.status._lock:
            if self.status.running:
                return
            self.status.running = True
        self._stop_event.clear()
        fyers_stream_service.start()
        if self._rank_thread is None or not self._rank_thread.is_alive():
            self._rank_thread = threading.Thread(target=self._rank_loop, name="rank-recalc-loop", daemon=True)
            self._rank_thread.start()
        if self._poll_thread is None or not self._poll_thread.is_alive():
            self._poll_thread = threading.Thread(target=self._poll_fallback_loop, name="watch-poll-fallback", daemon=True)
            self._poll_thread.start()
        if self._flush_thread is None or not self._flush_thread.is_alive():
            self._flush_thread = threading.Thread(target=self._flush_loop, name="ltp-flush-loop", daemon=True)
            self._flush_thread.start()
        logger.info("Live price service started (watchlist stream)")

    def stop(self) -> None:
        self._stop_event.set()
        if self._watch_debounce_timer:
            self._watch_debounce_timer.cancel()
        fyers_stream_service.stop()
        with self.status._lock:
            self.status.running = False
            self.status.stream_connected = False

    def set_connection_watch(self, connection_id: int, scrips: list[str]) -> int:
        normalized = {s.strip().upper() for s in scrips if s and s.strip()}
        if len(normalized) > MAX_WATCH_SYMBOLS:
            normalized = set(sorted(normalized)[:MAX_WATCH_SYMBOLS])

        with self._watch_lock:
            if connection_id != 0:
                self._connection_watches.pop(0, None)
            if self._connection_watches.get(connection_id) == normalized:
                with self.status._lock:
                    return self.status.watch_count
            self._connection_watches[connection_id] = normalized

        return self._schedule_watch_sync()

    def remove_connection(self, connection_id: int) -> None:
        with self._watch_lock:
            self._connection_watches.pop(connection_id, None)
        self._schedule_watch_sync()

    def set_watch_scrips(self, scrips: list[str]) -> int:
        return self.set_connection_watch(0, scrips)

    def _active_watch(self) -> list[str]:
        with self._watch_lock:
            union: set[str] = set()
            for scrips in self._connection_watches.values():
                union.update(scrips)
        return sorted(union)[:MAX_WATCH_SYMBOLS]

    def _schedule_watch_sync(self) -> int:
        scrips = self._active_watch()
        with self.status._lock:
            self.status.watch_count = len(scrips)

        if self._watch_debounce_timer:
            self._watch_debounce_timer.cancel()
        self._watch_debounce_timer = threading.Timer(WATCH_DEBOUNCE_SEC, self._apply_watch_sync)
        self._watch_debounce_timer.daemon = True
        self._watch_debounce_timer.start()
        return len(scrips)

    def _apply_watch_sync(self) -> None:
        scrips = self._active_watch()
        key = ",".join(scrips)
        if key == self._last_synced_key:
            return
        self._last_synced_key = key

        self._rebuild_scrip_maps(scrips)
        fyers_stream_service.sync_watchlist(scrips)
        with self.status._lock:
            self.status.stream_connected = fyers_stream_service.connected
            self.status.mode = "stream" if fyers_stream_service.connected else "poll"
            self.status.watch_count = len(scrips)

    def _rebuild_scrip_maps(self, scrips: list[str]) -> None:
        if not scrips:
            with self._ltp_cache_lock:
                self._scrip_to_live_id = {}
            return

        db = SessionLocal()
        try:
            stocks = list(db.scalars(select(Stock).where(Stock.scrip.in_(set(scrips)))))
            mapping: dict[str, int] = {}
            for stock in stocks:
                mapping[stock.scrip.upper()] = stock.id
        finally:
            db.close()

        with self._ltp_cache_lock:
            self._scrip_to_live_id = mapping

    def register_ws_queue(self, queue: Queue) -> None:
        with self._ws_lock:
            self._ws_queues.append(queue)
            with self.status._lock:
                self.status.subscribers = len(self._ws_queues)

    def unregister_ws_queue(self, queue: Queue) -> None:
        with self._ws_lock:
            if queue in self._ws_queues:
                self._ws_queues.remove(queue)
            with self.status._lock:
                self.status.subscribers = len(self._ws_queues)

    def _broadcast(self, payload: dict) -> None:
        with self._ws_lock:
            queues = list(self._ws_queues)
        for tick_queue in queues:
            try:
                tick_queue.put_nowait(payload)
            except Full:
                pass

    def _record_tick(self, scrip: str, ltp: float, pct_change: float | None, source: str) -> None:
        key = scrip.upper()
        with self._ltp_cache_lock:
            self._scrip_ltps[key] = {
                "ltp": ltp,
                "pct_change": pct_change,
                "source": source,
                "at": datetime.utcnow().isoformat(),
            }
            live_id = self._scrip_to_live_id.get(key)
            if live_id:
                self._ltp_cache[live_id] = ltp

    def _on_stream_tick(self, scrip: str, ltp: float, pct_change: float | None) -> None:
        now = datetime.utcnow()
        self._record_tick(scrip, ltp, pct_change, "fyers_stream")
        with self.status._lock:
            self.status.last_run_at = now
            self.status.last_updated = 1
            self.status.last_quote_source = "fyers_stream"
            self.status.total_ticks += 1
            self.status.stream_connected = True
            self.status.mode = "stream"

        self._broadcast(
            {
                "type": "price_tick",
                "updated": 1,
                "at": now.isoformat(),
                "prices": [
                    {
                        "scrip": scrip,
                        "ltp": ltp,
                        "pct_change": pct_change,
                        "source": "fyers_stream",
                        "at": now.isoformat(),
                    }
                ],
            }
        )

    def _flush_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(LTP_FLUSH_INTERVAL_SEC):
                break
            with self._ltp_cache_lock:
                if not self._ltp_cache:
                    continue
                batch = dict(self._ltp_cache)
                self._ltp_cache.clear()

            db = SessionLocal()
            try:
                def _flush() -> None:
                    patch_live_ltps_only(db, batch)
                    commit_session(db, label="ltp flush")

                run_write_with_retry(_flush, label="ltp flush")
            except Exception as exc:  # noqa: BLE001
                logger.warning("LTP flush failed: %s", exc)
                db.rollback()
            finally:
                db.close()

    def refresh_by_scrips(self, scrips: list[str], *, broadcast: bool = True, persist: bool = False) -> dict:
        if not scrips:
            return {"type": "price_tick", "updated": 0, "at": datetime.utcnow().isoformat(), "prices": []}

        db = SessionLocal()
        try:
            normalized = {s.strip().upper() for s in scrips if s and s.strip()}
            requested = list(db.scalars(select(Stock).where(Stock.scrip.in_(normalized))))
            if not requested:
                return {"type": "price_tick", "updated": 0, "at": datetime.utcnow().isoformat(), "prices": []}

            quotes = fetch_live_quotes_for_stocks(db, requested)

            tick_rows: list[dict] = []
            updated = 0
            ltp_by_stock: dict[int, float] = {}
            now = datetime.utcnow()
            last_source: str | None = None

            for req in requested:
                quote = quotes.get(req.id)
                if not quote or quote.ltp is None:
                    continue

                last_source = getattr(quote, "source", None) or last_source
                ltp_by_stock[req.id] = quote.ltp
                self._record_tick(req.scrip, quote.ltp, quote.pct_change, quote.source or "poll")

                tick_rows.append(
                    {
                        "scrip": req.scrip,
                        "ltp": quote.ltp,
                        "pct_change": quote.pct_change,
                        "source": quote.source,
                        "at": now.isoformat(),
                    }
                )
                updated += 1

            if persist and ltp_by_stock:
                with serialized_write():
                    patch_live_ltps_only(db, ltp_by_stock)
                    commit_session(db, label="refresh quotes")

            if last_source:
                with self.status._lock:
                    self.status.last_quote_source = last_source

            payload = {
                "type": "price_tick",
                "updated": updated,
                "at": now.isoformat(),
                "prices": tick_rows,
            }
            if broadcast and tick_rows:
                self._broadcast(payload)
            return payload
        finally:
            db.close()

    def _poll_fallback_loop(self) -> None:
        """HTTP quote backup for watched symbols (fills gaps when stream misses)."""
        while not self._stop_event.is_set():
            stream_up = fyers_stream_service.connected
            wait_sec = 15 if stream_up else POLL_FALLBACK_INTERVAL_SEC
            if self._stop_event.wait(wait_sec):
                break

            scrips = self._active_watch()
            if not scrips:
                continue

            started = datetime.now(timezone.utc)
            try:
                payload = self.refresh_by_scrips(scrips, broadcast=True, persist=False)
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                with self.status._lock:
                    self.status.last_run_at = started
                    self.status.last_duration_sec = round(elapsed, 2)
                    self.status.last_updated = payload.get("updated", 0)
                    self.status.last_error = None
                    self.status.total_ticks += 1
                    self.status.mode = "stream" if stream_up else "poll"
                    self.status.stream_connected = stream_up
            except Exception as exc:  # noqa: BLE001
                logger.warning("Watch poll failed: %s", exc)
                with self.status._lock:
                    self.status.last_error = str(exc)

    def _rank_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(RANK_RECALC_INTERVAL_SEC):
                break

            db = SessionLocal()
            try:
                def _recalc() -> None:
                    recalculate_rankings_from_db(db)
                    commit_session(db, label="rank recalc")
                    invalidate_universe_cache()

                run_write_with_retry(_recalc, label="rank recalc")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Background rank recalc failed: %s", exc)
                db.rollback()
            finally:
                db.close()

    def get_latest_prices(self, scrips: list[str] | None = None) -> list[dict]:
        overlay = self.get_scrip_ltps()
        if scrips:
            return [
                {
                    "scrip": s,
                    "ltp": overlay[s.upper()]["ltp"],
                    "pct_change": overlay[s.upper()].get("pct_change"),
                    "as_of": overlay[s.upper()].get("at"),
                }
                for s in scrips
                if s.upper() in overlay
            ]
        return [{"scrip": k, **v} for k, v in overlay.items()]


live_price_service = LivePriceService()
