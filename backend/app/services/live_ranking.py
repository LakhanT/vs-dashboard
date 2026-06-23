"""In-memory Y/Q/M rankings from live LTP — recalculated on every tick."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OhlcBar, Timeframe
from app.services.ohlc_builder import effective_high_low
from app.services.rank_utils import excel_rank_eq_desc
from app.services.universe import get_rsi_universe_stocks

logger = logging.getLogger(__name__)

_RANK_TIMEFRAMES = (Timeframe.YEARLY, Timeframe.QUARTERLY, Timeframe.MONTHLY)
_PERIOD_PREFIX = {
    Timeframe.YEARLY: "y",
    Timeframe.QUARTERLY: "q",
    Timeframe.MONTHLY: "m",
}


@dataclass(frozen=True)
class PeriodBar:
    open: float
    high: float
    low: float
    close: float | None = None


@dataclass(frozen=True)
class LiveRankRow:
    y_rank: int | None = None
    q_rank: int | None = None
    m_rank: int | None = None
    y_open: float | None = None
    y_high: float | None = None
    y_low: float | None = None
    y_close: float | None = None
    q_open: float | None = None
    q_high: float | None = None
    q_low: float | None = None
    q_close: float | None = None
    m_open: float | None = None
    m_high: float | None = None
    m_low: float | None = None
    m_close: float | None = None
    y_pct_change_open: float | None = None
    q_pct_change_open: float | None = None
    m_pct_change_open: float | None = None
    y_high_retracement: float | None = None
    green_range: float | None = None
    retracement_from_high: float | None = None
    rise_from_low: float | None = None
    bullish_bo: float | None = None


def _live_rank_row_payload(row: LiveRankRow) -> dict[str, float | int | None]:
    return {
        "y_rank": row.y_rank,
        "q_rank": row.q_rank,
        "m_rank": row.m_rank,
        "y_open": row.y_open,
        "y_high": row.y_high,
        "y_low": row.y_low,
        "y_close": row.y_close,
        "q_open": row.q_open,
        "q_high": row.q_high,
        "q_low": row.q_low,
        "q_close": row.q_close,
        "m_open": row.m_open,
        "m_high": row.m_high,
        "m_low": row.m_low,
        "m_close": row.m_close,
        "y_pct_change_open": row.y_pct_change_open,
        "q_pct_change_open": row.q_pct_change_open,
        "m_pct_change_open": row.m_pct_change_open,
        "y_high_retracement": row.y_high_retracement,
        "green_range": row.green_range,
        "retracement_from_high": row.retracement_from_high,
        "rise_from_low": row.rise_from_low,
        "bullish_bo": row.bullish_bo,
    }


class LiveRankingCache:
    """Universe-wide live ranks derived from period opens + latest LTP."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bars: dict[int, dict[Timeframe, PeriodBar]] = {}
        self._pre_year_high: dict[int, float] = {}
        self._ltps: dict[int, float] = {}
        self._id_to_scrip: dict[int, str] = {}
        self._scrip_to_id: dict[str, int] = {}
        self._rows: dict[str, LiveRankRow] = {}
        self._revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def loaded(self) -> bool:
        with self._lock:
            return bool(self._bars)

    def load_from_db(self, db: Session) -> int:
        stocks = get_rsi_universe_stocks(db)
        bars_by_stock: dict[int, dict[Timeframe, PeriodBar]] = {}
        pre_year_high: dict[int, float] = {}
        ltps: dict[int, float] = {}
        id_to_scrip: dict[int, str] = {}
        scrip_to_id: dict[str, int] = {}

        for stock in stocks:
            if not stock.scrip:
                continue
            key = stock.scrip.upper()
            id_to_scrip[stock.id] = stock.scrip
            scrip_to_id[key] = stock.id

            current_bars = db.scalars(
                select(OhlcBar).where(OhlcBar.stock_id == stock.id, OhlcBar.is_current.is_(True))
            ).all()
            stock_bars: dict[Timeframe, PeriodBar] = {}
            for bar in current_bars:
                if bar.timeframe not in _RANK_TIMEFRAMES or bar.open is None:
                    continue
                high = bar.high if bar.high is not None else bar.open
                low = bar.low if bar.low is not None else bar.open
                stock_bars[bar.timeframe] = PeriodBar(
                    open=float(bar.open),
                    high=float(high),
                    low=float(low),
                    close=float(bar.close or bar.lcp) if (bar.close is not None or bar.lcp is not None) else None,
                )
                if bar.lcp is not None:
                    ltps[stock.id] = float(bar.lcp)

            pre_bar = db.scalar(
                select(OhlcBar).where(
                    OhlcBar.stock_id == stock.id,
                    OhlcBar.timeframe == Timeframe.YEARLY,
                    OhlcBar.period_label == "Pre Yearly",
                )
            )
            if pre_bar and pre_bar.high is not None:
                pre_year_high[stock.id] = float(pre_bar.high)

            if stock_bars:
                bars_by_stock[stock.id] = stock_bars

        with self._lock:
            self._bars = bars_by_stock
            self._pre_year_high = pre_year_high
            self._ltps = ltps
            self._id_to_scrip = id_to_scrip
            self._scrip_to_id = scrip_to_id
            self._recompute_locked()
            self._revision += 1
            count = len(self._rows)

        logger.info("Live ranking cache loaded: %s stocks", count)
        return count

    def update_ltp(self, stock_id: int, ltp: float) -> int:
        with self._lock:
            if stock_id not in self._bars:
                return self._revision
            self._ltps[stock_id] = ltp
            self._recompute_locked()
            self._revision += 1
            return self._revision

    def update_ltps(self, ltp_by_stock: dict[int, float]) -> int:
        with self._lock:
            changed = False
            for stock_id, ltp in ltp_by_stock.items():
                if stock_id not in self._bars:
                    continue
                self._ltps[stock_id] = ltp
                changed = True
            if not changed:
                return self._revision
            self._recompute_locked()
            self._revision += 1
            return self._revision

    def get_row(self, scrip: str) -> LiveRankRow | None:
        with self._lock:
            return self._rows.get(scrip.upper())

    def get_overlay_by_scrip(self) -> dict[str, dict[str, float | int | None]]:
        with self._lock:
            return {scrip: _live_rank_row_payload(row) for scrip, row in self._rows.items()}

    def snapshot_payload(self) -> list[dict]:
        with self._lock:
            payload: list[dict] = []
            for scrip_key, row in self._rows.items():
                scrip = self._id_to_scrip.get(self._scrip_to_id.get(scrip_key, -1), scrip_key)
                payload.append({"scrip": scrip, **_live_rank_row_payload(row)})
            return payload

    def _recompute_locked(self) -> None:
        """
        Excel formulas (Y/Q/M rank sheets):
          % change open = (LCP - period.open) / period.open
          Live Ranking  = RANK.EQ(% change open, universe) descending
        """
        pct_by_tf: dict[Timeframe, dict[int, float]] = {tf: {} for tf in _RANK_TIMEFRAMES}
        metrics_by_stock: dict[int, dict[str, float | None]] = {}

        for stock_id, tf_bars in self._bars.items():
            ltp = self._ltps.get(stock_id)
            if ltp is None:
                continue

            stock_metrics: dict[str, float | None] = {}
            for tf in _RANK_TIMEFRAMES:
                bar = tf_bars.get(tf)
                if not bar:
                    continue
                prefix = _PERIOD_PREFIX[tf]
                eff_high, eff_low = effective_high_low(
                    open_price=bar.open,
                    high=bar.high,
                    low=bar.low,
                    lcp=ltp,
                )
                pct = (ltp - bar.open) / bar.open if bar.open else 0.0
                stock_metrics[f"{prefix}_open"] = bar.open
                stock_metrics[f"{prefix}_high"] = eff_high
                stock_metrics[f"{prefix}_low"] = eff_low
                stock_metrics[f"{prefix}_close"] = ltp
                stock_metrics[f"{prefix}_pct_change_open"] = pct
                pct_by_tf[tf][stock_id] = pct

                if tf == Timeframe.YEARLY:
                    stock_metrics["y_high_retracement"] = (
                        (ltp - eff_high) / eff_high if eff_high else None
                    )
                    stock_metrics["retracement_from_high"] = stock_metrics["y_high_retracement"]
                    stock_metrics["rise_from_low"] = (ltp - eff_low) / eff_low if eff_low else None
                    stock_metrics["green_range"] = pct
                    pre_high = self._pre_year_high.get(stock_id)
                    stock_metrics["bullish_bo"] = (
                        (ltp - pre_high) / pre_high if pre_high else None
                    )

            if stock_metrics:
                metrics_by_stock[stock_id] = stock_metrics

        ranks_by_tf = {tf: excel_rank_eq_desc(pct_by_tf[tf]) for tf in _RANK_TIMEFRAMES}

        rows: dict[str, LiveRankRow] = {}
        for stock_id, metrics in metrics_by_stock.items():
            scrip = self._id_to_scrip.get(stock_id)
            if not scrip:
                continue
            rows[scrip.upper()] = LiveRankRow(
                y_rank=ranks_by_tf.get(Timeframe.YEARLY, {}).get(stock_id),
                q_rank=ranks_by_tf.get(Timeframe.QUARTERLY, {}).get(stock_id),
                m_rank=ranks_by_tf.get(Timeframe.MONTHLY, {}).get(stock_id),
                y_open=metrics.get("y_open"),  # type: ignore[arg-type]
                y_high=metrics.get("y_high"),  # type: ignore[arg-type]
                y_low=metrics.get("y_low"),  # type: ignore[arg-type]
                y_close=metrics.get("y_close"),  # type: ignore[arg-type]
                q_open=metrics.get("q_open"),  # type: ignore[arg-type]
                q_high=metrics.get("q_high"),  # type: ignore[arg-type]
                q_low=metrics.get("q_low"),  # type: ignore[arg-type]
                q_close=metrics.get("q_close"),  # type: ignore[arg-type]
                m_open=metrics.get("m_open"),  # type: ignore[arg-type]
                m_high=metrics.get("m_high"),  # type: ignore[arg-type]
                m_low=metrics.get("m_low"),  # type: ignore[arg-type]
                m_close=metrics.get("m_close"),  # type: ignore[arg-type]
                y_pct_change_open=metrics.get("y_pct_change_open"),  # type: ignore[arg-type]
                q_pct_change_open=metrics.get("q_pct_change_open"),  # type: ignore[arg-type]
                m_pct_change_open=metrics.get("m_pct_change_open"),  # type: ignore[arg-type]
                y_high_retracement=metrics.get("y_high_retracement"),  # type: ignore[arg-type]
                green_range=metrics.get("green_range"),  # type: ignore[arg-type]
                retracement_from_high=metrics.get("retracement_from_high"),  # type: ignore[arg-type]
                rise_from_low=metrics.get("rise_from_low"),  # type: ignore[arg-type]
                bullish_bo=metrics.get("bullish_bo"),  # type: ignore[arg-type]
            )

        self._rows = rows


live_ranking_cache = LiveRankingCache()


def reload_live_ranking_cache(db: Session) -> int:
    return live_ranking_cache.load_from_db(db)
