import { useCallback, useEffect, useRef, useState } from "react";
import type { LivePriceStatus, LivePriceTick, LiveRankSnapshotRow } from "./types";
import {
  fetchLivePriceStatus,
  livePricesWebSocketUrl,
  startLivePrices,
  stopLivePrices,
} from "./api";

type PriceTickHandler = (ticks: LivePriceTick[]) => void;
type RankSnapshotHandler = (rows: LiveRankSnapshotRow[], revision: number) => void;

export function useLivePrices(
  enabled: boolean,
  onTick: PriceTickHandler,
  onRankSnapshot?: RankSnapshotHandler,
) {
  const [status, setStatus] = useState<LivePriceStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const onTickRef = useRef(onTick);
  const onRankSnapshotRef = useRef(onRankSnapshot);
  const wsRef = useRef<WebSocket | null>(null);
  onTickRef.current = onTick;
  onRankSnapshotRef.current = onRankSnapshot;

  const refreshStatus = useCallback(async () => {
    const next = await fetchLivePriceStatus();
    setStatus(next);
    return next;
  }, []);

  const markRefreshing = useCallback(() => {
    setRefreshing(true);
    window.setTimeout(() => setRefreshing(false), 600);
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (enabled) return;
    setConnected(false);
    setRefreshing(false);
    void stopLivePrices().then(setStatus).catch(() => undefined);
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;

    let closed = false;
    let reconnectTimer: number | undefined;

    void startLivePrices().then(setStatus).catch(() => undefined);

    function connect() {
      if (closed) return;
      const ws = new WebSocket(livePricesWebSocketUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data as string) as {
            type?: string;
            status?: LivePriceStatus;
            prices?: LivePriceTick[];
            ranks?: LiveRankSnapshotRow[];
            revision?: number;
          };
          if (payload.type === "connected" && payload.status) {
            setStatus(payload.status);
          }
          if (payload.type === "subscribed" && payload.status) {
            setStatus(payload.status);
          }
          if (payload.type === "heartbeat" && payload.status) {
            setStatus(payload.status);
          }
          if (payload.type === "price_tick" && payload.prices?.length) {
            markRefreshing();
            onTickRef.current(payload.prices);
          }
          if (payload.type === "rank_snapshot" && payload.ranks?.length && onRankSnapshotRef.current) {
            onRankSnapshotRef.current(payload.ranks, payload.revision ?? 0);
          }
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    }

    connect();

    const statusPoll = window.setInterval(() => {
      void refreshStatus();
    }, 15000);

    return () => {
      closed = true;
      window.clearInterval(statusPoll);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, refreshStatus, markRefreshing]);

  return { status, connected, refreshing, refreshStatus };
}
