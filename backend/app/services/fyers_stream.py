"""Fyers DataSocket — real-time SymbolUpdate stream (Zerodha-style watchlist)."""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Stock
from app.services.fyers import fyers_configured
from app.services.fyers_auth import ensure_access_token, resolve_app_credentials
from app.services.stock_resolver import quote_stock_candidates
from app.services.symbol_utils import to_fyers_symbol

logger = logging.getLogger(__name__)

TickHandler = Callable[[str, float, float | None], None]


class FyersStreamService:
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._fyers_ws = None
    self._thread: threading.Thread | None = None
    self._stop = threading.Event()
    self._subscribed_fyers: set[str] = set()
    self._fyers_to_scrip: dict[str, str] = {}
    self._connected = False
    self._on_tick: TickHandler | None = None

  @property
  def connected(self) -> bool:
    with self._lock:
      return self._connected

  def set_tick_handler(self, handler: TickHandler | None) -> None:
    self._on_tick = handler

  def start(self) -> None:
    if not fyers_configured():
      return
    with self._lock:
      if self._thread and self._thread.is_alive():
        return
    self._stop.clear()
    self._thread = threading.Thread(target=self._run, name="fyers-data-socket", daemon=True)
    self._thread.start()

  def stop(self) -> None:
    self._stop.set()
    ws = self._fyers_ws
    if ws is not None:
      try:
        ws.close_connection()
      except Exception:  # noqa: BLE001
        pass
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=5)
    self._thread = None
    with self._lock:
      self._connected = False
      self._subscribed_fyers.clear()

  def sync_watchlist(self, scrips: list[str]) -> int:
    """Subscribe/unsubscribe Fyers symbols for the union watchlist."""
    if not fyers_configured():
      return 0

    normalized = sorted({s.strip().upper() for s in scrips if s and s.strip()})[:100]
    if not normalized:
      with self._lock:
        self._fyers_to_scrip = {}
      self._apply_subscription_diff(set())
      return 0

    desired_fyers: dict[str, str] = {}
    db = SessionLocal()
    try:
      stocks = list(db.scalars(select(Stock).where(Stock.scrip.in_(set(normalized)))))
      for stock in stocks:
        for candidate in quote_stock_candidates(db, stock):
          fyers_sym = to_fyers_symbol(candidate)
          if fyers_sym:
            desired_fyers[fyers_sym.upper()] = stock.scrip
    finally:
      db.close()

    new_key = ",".join(sorted(desired_fyers.keys()))
    with self._lock:
      if getattr(self, "_last_watch_key", None) == new_key:
        return len(desired_fyers)
      self._last_watch_key = new_key
      self._fyers_to_scrip = desired_fyers

    self._apply_subscription_diff(set(desired_fyers.keys()))
    if not self._thread or not self._thread.is_alive():
      self.start()
    return len(desired_fyers)

  def _apply_subscription_diff(self, desired: set[str]) -> None:
    ws = self._fyers_ws
    if ws is None:
      return
    with self._lock:
      current = set(self._subscribed_fyers)
    to_add = list(desired - current)
    to_remove = list(current - desired)
    try:
      if to_remove:
        ws.unsubscribe(symbols=to_remove, data_type="SymbolUpdate")
      if to_add:
        ws.subscribe(symbols=to_add, data_type="SymbolUpdate")
      with self._lock:
        self._subscribed_fyers = desired
      if to_add or to_remove:
        logger.info("Fyers stream watchlist: %s symbols (%s added, %s removed)", len(desired), len(to_add), len(to_remove))
    except Exception as exc:  # noqa: BLE001
      logger.warning("Fyers subscribe update failed: %s", exc)

  def _run(self) -> None:
    access_token = ensure_access_token()
    if not access_token:
      logger.info("Fyers stream not started — no access token")
      return

    client_id, _, _ = resolve_app_credentials()
    token = f"{client_id}:{access_token}"

    try:
      from fyers_apiv3.FyersWebsocket import data_ws
    except Exception as exc:  # noqa: BLE001
      logger.warning("Fyers websocket module unavailable: %s", exc)
      return

    def on_connect() -> None:
      with self._lock:
        self._connected = True
      logger.info("Fyers data socket connected")
      with self._lock:
        symbols = list(self._fyers_to_scrip.keys())
      if symbols:
        try:
          self._fyers_ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
          with self._lock:
            self._subscribed_fyers = set(symbols)
          logger.info("Fyers stream subscribed to %s symbols", len(symbols))
        except Exception as exc:  # noqa: BLE001
          logger.warning("Fyers initial subscribe failed: %s", exc)

    def on_close(_msg=None) -> None:
      with self._lock:
        self._connected = False
        self._subscribed_fyers.clear()
      logger.info("Fyers data socket closed")

    def on_error(msg) -> None:
      logger.warning("Fyers data socket error: %s", msg)

    def on_message(message) -> None:
      if self._stop.is_set():
        return
      self._handle_message(message)

    try:
      self._fyers_ws = data_ws.FyersDataSocket(
        access_token=token,
        log_path="",
        litemode=True,
        write_to_file=False,
        reconnect=True,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
      )
      self._fyers_ws.connect()
      self._fyers_ws.keep_running()
    except Exception as exc:  # noqa: BLE001
      logger.exception("Fyers stream thread ended: %s", exc)
    finally:
      with self._lock:
        self._connected = False

  def _handle_message(self, message) -> None:
    if not isinstance(message, dict):
      return

    fyers_symbol = (
      message.get("symbol")
      or message.get("n")
      or message.get("sym")
      or (message.get("d") or {}).get("symbol")
    )
    if not fyers_symbol:
      return

    values = message.get("v") if isinstance(message.get("v"), dict) else message
    ltp = _to_float(values.get("ltp") if isinstance(values, dict) else None) or _to_float(
      values.get("lp") if isinstance(values, dict) else None
    ) or _to_float(message.get("ltp")) or _to_float(message.get("lp"))
    if ltp is None:
      return

    chp = _to_float(values.get("chp") if isinstance(values, dict) else None) or _to_float(message.get("chp"))
    pct_change = chp / 100.0 if chp is not None else None

    key = str(fyers_symbol).upper()
    with self._lock:
      scrip = self._fyers_to_scrip.get(key)
      if not scrip and ":" in key:
        scrip = self._fyers_to_scrip.get(key.split(":", 1)[1])
    if not scrip or not self._on_tick:
      return

    self._on_tick(scrip, ltp, pct_change)


def _to_float(value) -> float | None:
  if value is None or value == "":
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


fyers_stream_service = FyersStreamService()
