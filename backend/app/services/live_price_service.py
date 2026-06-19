"""Live prices via Fyers — full universe, parallel async HTTP quotes."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from queue import Full, Queue

from app.config import get_settings
from app.database import SessionLocal
from app.db_write import commit_session, run_write_with_retry, serialized_write
from app.services.dashboard_service import invalidate_universe_cache
from app.services.market_data import fetch_fyers_quotes_for_stocks_parallel
from app.services.ranking_engine import patch_live_ltps_only, recalculate_rankings_from_db
from app.services.universe import get_rsi_universe_stocks

logger = logging.getLogger(__name__)
settings = get_settings()

RANK_RECALC_CHECK_SEC = 5
RANK_RECALC_DEBOUNCE_SEC = 12
LTP_FLUSH_INTERVAL_SEC = 8
UNIVERSE_REFRESH_SEC = 120


@dataclass
class LivePriceStatus:
    running: bool = False
    mode: str = "async"
    interval_sec: int = 5
    watch_count: int = 0
    stream_connected: bool = False
    last_run_at: datetime | None = None
    last_duration_sec: float | None = None
    last_updated: int = 0
    last_error: str | None = None
    last_quote_source: str | None = None
    total_ticks: int = 0
    subscribers: int = 0
    universe_count: int = 0
    batch_size: int = 0
    parallel_workers: int = 0
    cursor: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "mode": self.mode,
                "interval_sec": self.interval_sec,
                "watch_count": self.universe_count,
                "stream_connected": self.stream_connected,
                "batch_size": self.batch_size,
                "universe_count": self.universe_count,
                "parallel_workers": self.parallel_workers,
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
        self._universe_lock = threading.Lock()
        self._universe_stocks: list = []
        self._universe_scrips: list[str] = []
        self._universe_scrip_set: set[str] = set()
        self._last_universe_refresh = 0.0
        self._ltp_cache: dict[int, float] = {}
        self._scrip_ltps: dict[str, dict] = {}
        self._scrip_to_live_id: dict[str, int] = {}
        self._ltp_cache_lock = threading.Lock()
        self._rank_recalc_pending = False
        self._rank_recalc_lock = threading.Lock()
        self._last_rank_recalc_at: datetime | None = None
        self._poll_in_flight = False
        self._poll_lock = threading.Lock()

    def get_scrip_ltps(self) -> dict[str, dict]:
        with self._ltp_cache_lock:
            return dict(self._scrip_ltps)

    def refresh_universe(self) -> int:
        """Load all RSI universe scrips and rebuild id maps."""
        db = SessionLocal()
        try:
            stocks = get_rsi_universe_stocks(db)
            scrips = [s.scrip for s in stocks if s.scrip]
            mapping: dict[str, int] = {s.scrip.upper(): s.id for s in stocks if s.scrip}
        finally:
            db.close()

        with self._universe_lock:
            self._universe_stocks = stocks
            self._universe_scrips = scrips
            self._universe_scrip_set = {s.upper() for s in scrips}

        with self._ltp_cache_lock:
            self._scrip_to_live_id = mapping
            active_keys = self._universe_scrip_set
            for key in list(self._scrip_ltps.keys()):
                if key not in active_keys:
                    del self._scrip_ltps[key]
            active_ids = set(mapping.values())
            for stock_id in list(self._ltp_cache.keys()):
                if stock_id not in active_ids:
                    del self._ltp_cache[stock_id]

        batch_size = max(1, settings.live_price_batch_size)
        workers = max(1, settings.live_price_parallel_workers)
        with self.status._lock:
            self.status.universe_count = len(scrips)
            self.status.watch_count = len(scrips)
            self.status.batch_size = batch_size
            self.status.parallel_workers = workers

        self._last_universe_refresh = datetime.now(timezone.utc).timestamp()
        logger.info(
            "Live price universe: %s scrips (%s parallel workers, batch %s)",
            len(scrips),
            workers,
            batch_size,
        )
        return len(scrips)

    def start(self) -> None:
        with self.status._lock:
            if self.status.running:
                return
            self.status.running = True
            self.status.mode = "async"
            self.status.batch_size = max(1, settings.live_price_batch_size)
            self.status.parallel_workers = max(1, settings.live_price_parallel_workers)
        self._stop_event.clear()
        self.refresh_universe()
        if self._rank_thread is None or not self._rank_thread.is_alive():
            self._rank_thread = threading.Thread(target=self._rank_loop, name="rank-recalc-loop", daemon=True)
            self._rank_thread.start()
        if self._poll_thread is None or not self._poll_thread.is_alive():
            self._poll_thread = threading.Thread(target=self._universe_poll_loop, name="universe-async-poll", daemon=True)
            self._poll_thread.start()
        if self._flush_thread is None or not self._flush_thread.is_alive():
            self._flush_thread = threading.Thread(target=self._flush_loop, name="ltp-flush-loop", daemon=True)
            self._flush_thread.start()
        logger.info("Live price service started (parallel async universe)")

    def stop(self) -> None:
        self._stop_event.set()
        with self.status._lock:
            self.status.running = False
            self.status.stream_connected = False

    def set_connection_watch(self, connection_id: int, scrips: list[str]) -> int:
        with self.status._lock:
            return self.status.universe_count

    def remove_connection(self, connection_id: int) -> None:
        return

    def set_watch_scrips(self, scrips: list[str]) -> int:
        return self.refresh_universe()

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

    def _emit_tick(self, scrip: str, ltp: float, pct_change: float | None, source: str) -> None:
        self._record_tick(scrip, ltp, pct_change, source)
        now = datetime.utcnow()
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
                        "source": source,
                        "at": now.isoformat(),
                    }
                ],
            }
        )

    def _mark_rank_recalc_pending(self) -> None:
        with self._rank_recalc_lock:
            self._rank_recalc_pending = True

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
                self._mark_rank_recalc_pending()
            except Exception as exc:  # noqa: BLE001
                logger.warning("LTP flush failed: %s", exc)
                db.rollback()
            finally:
                db.close()

    def _fetch_universe_parallel(self) -> int:
        """Fetch all universe LTPs in parallel; broadcast ticks as batches complete."""
        with self._universe_lock:
            stocks = list(self._universe_stocks)

        if not stocks:
            return 0

        db = SessionLocal()
        ltp_by_stock: dict[int, float] = {}
        updated = 0
        id_to_stock = {s.id: s for s in stocks}

        def _on_batch(mapped: dict[int, object]) -> None:
            nonlocal updated
            for stock_id, quote in mapped.items():
                stock = id_to_stock.get(stock_id)
                if stock is None or quote.ltp is None:
                    continue
                ltp_by_stock[stock_id] = quote.ltp
                self._emit_tick(
                    stock.scrip,
                    quote.ltp,
                    quote.pct_change,
                    getattr(quote, "source", None) or "fyers_async",
                )
                updated += 1

        try:
            fetch_fyers_quotes_for_stocks_parallel(
                db,
                stocks,
                max_workers=settings.live_price_parallel_workers,
                on_batch=_on_batch,
            )

            if ltp_by_stock:
                with serialized_write():
                    patch_live_ltps_only(db, ltp_by_stock)
                    commit_session(db, label="async universe refresh")
                self._mark_rank_recalc_pending()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Parallel universe fetch failed: %s", exc)
            with self.status._lock:
                self.status.last_error = str(exc)
            db.rollback()
        finally:
            db.close()

        return updated

    def refresh_by_scrips(self, scrips: list[str], *, broadcast: bool = True, persist: bool = False) -> dict:
        if not scrips:
            return {"type": "price_tick", "updated": 0, "at": datetime.utcnow().isoformat(), "prices": []}

        db = SessionLocal()
        try:
            from sqlalchemy import select

            from app.models import Stock

            normalized = {s.strip().upper() for s in scrips if s and s.strip()}
            requested = list(db.scalars(select(Stock).where(Stock.scrip.in_(normalized))))
            if not requested:
                return {"type": "price_tick", "updated": 0, "at": datetime.utcnow().isoformat(), "prices": []}

            tick_rows: list[dict] = []
            updated = 0
            ltp_by_stock: dict[int, float] = {}
            now = datetime.utcnow()

            def _on_batch(mapped: dict[int, object]) -> None:
                nonlocal updated
                for stock in requested:
                    quote = mapped.get(stock.id)
                    if not quote or quote.ltp is None:
                        continue
                    ltp_by_stock[stock.id] = quote.ltp
                    row = {
                        "scrip": stock.scrip,
                        "ltp": quote.ltp,
                        "pct_change": quote.pct_change,
                        "source": getattr(quote, "source", None) or "fyers_async",
                        "at": now.isoformat(),
                    }
                    tick_rows.append(row)
                    updated += 1
                    if broadcast:
                        self._emit_tick(stock.scrip, quote.ltp, quote.pct_change, row["source"])

            quotes = fetch_fyers_quotes_for_stocks_parallel(
                db,
                requested,
                on_batch=_on_batch if broadcast else None,
            )
            if not broadcast:
                for stock in requested:
                    quote = quotes.get(stock.id)
                    if not quote or quote.ltp is None:
                        continue
                    ltp_by_stock[stock.id] = quote.ltp
                    tick_rows.append(
                        {
                            "scrip": stock.scrip,
                            "ltp": quote.ltp,
                            "pct_change": quote.pct_change,
                            "source": getattr(quote, "source", None) or "fyers_async",
                            "at": now.isoformat(),
                        }
                    )
                    updated += 1

            if persist and ltp_by_stock:
                with serialized_write():
                    patch_live_ltps_only(db, ltp_by_stock)
                    commit_session(db, label="refresh quotes")
                self._mark_rank_recalc_pending()

            with self.status._lock:
                self.status.last_quote_source = "fyers_async"

            return {
                "type": "price_tick",
                "updated": updated,
                "at": now.isoformat(),
                "prices": tick_rows,
            }
        finally:
            db.close()

    def _universe_poll_loop(self) -> None:
        """Refresh the entire universe in parallel; repeat after interval."""
        interval = max(1, settings.live_price_interval_sec)
        while not self._stop_event.is_set():
            with self._poll_lock:
                if self._poll_in_flight:
                    if self._stop_event.wait(0.5):
                        break
                    continue
                self._poll_in_flight = True

            now_ts = datetime.now(timezone.utc).timestamp()
            if now_ts - self._last_universe_refresh >= UNIVERSE_REFRESH_SEC:
                self.refresh_universe()

            started = datetime.now(timezone.utc)
            try:
                updated = self._fetch_universe_parallel()
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                with self.status._lock:
                    self.status.last_run_at = started
                    self.status.last_duration_sec = round(elapsed, 2)
                    self.status.last_updated = updated
                    self.status.last_error = None
                    self.status.total_ticks += updated
                    self.status.mode = "async"
                    self.status.stream_connected = False
            except Exception as exc:  # noqa: BLE001
                logger.warning("Universe async poll failed: %s", exc)
                with self.status._lock:
                    self.status.last_error = str(exc)
            finally:
                with self._poll_lock:
                    self._poll_in_flight = False

            sleep_for = max(0.0, interval - (datetime.now(timezone.utc) - started).total_seconds())
            if self._stop_event.wait(sleep_for):
                break

    def _rank_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(RANK_RECALC_CHECK_SEC):
                break

            with self._rank_recalc_lock:
                if not self._rank_recalc_pending:
                    continue
                last_at = self._last_rank_recalc_at
            if last_at and (datetime.utcnow() - last_at).total_seconds() < RANK_RECALC_DEBOUNCE_SEC:
                continue

            db = SessionLocal()
            try:
                def _recalc() -> None:
                    recalculate_rankings_from_db(db)
                    commit_session(db, label="rank recalc")
                    invalidate_universe_cache()

                run_write_with_retry(_recalc, label="rank recalc")
                with self._rank_recalc_lock:
                    self._rank_recalc_pending = False
                    self._last_rank_recalc_at = datetime.utcnow()
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
