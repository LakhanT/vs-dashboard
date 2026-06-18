import { useCallback, useEffect, useRef, useState } from "react";
import type { LivePriceStatus, LivePriceTick } from "./types";
import {
  fetchLivePriceStatus,
  livePricesWebSocketUrl,
  startLivePrices,
  stopLivePrices,
} from "./api";

type PriceTickHandler = (ticks: LivePriceTick[]) => void;

const SUBSCRIBE_DEBOUNCE_MS = 500;

export function useLivePrices(
  enabled: boolean,
  onTick: PriceTickHandler,
  visibleScrips: string[],
) {
  const [status, setStatus] = useState<LivePriceStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const onTickRef = useRef(onTick);
  const wsRef = useRef<WebSocket | null>(null);
  const scripsKeyRef = useRef("");
  const subscribeTimerRef = useRef<number | undefined>(undefined);
  const visibleScripsRef = useRef(visibleScrips);
  visibleScripsRef.current = visibleScrips;
  onTickRef.current = onTick;

  const refreshStatus = useCallback(async () => {
    const next = await fetchLivePriceStatus();
    setStatus(next);
    return next;
  }, []);

  const markRefreshing = useCallback(() => {
    setRefreshing(true);
    window.setTimeout(() => setRefreshing(false), 600);
  }, []);

  const sendSubscribe = useCallback(() => {
    const ws = wsRef.current;
    const scrips = visibleScripsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !scrips.length) return;
    const key = scrips.join(",");
    if (key === scripsKeyRef.current) return;
    scripsKeyRef.current = key;
    ws.send(JSON.stringify({ type: "subscribe", scrips }));
  }, []);

  const scheduleSubscribe = useCallback(() => {
    if (subscribeTimerRef.current) {
      window.clearTimeout(subscribeTimerRef.current);
    }
    subscribeTimerRef.current = window.setTimeout(() => {
      sendSubscribe();
    }, SUBSCRIBE_DEBOUNCE_MS);
  }, [sendSubscribe]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (enabled) return;
    setConnected(false);
    setRefreshing(false);
    scripsKeyRef.current = "";
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
      scripsKeyRef.current = "";

      ws.onopen = () => {
        setConnected(true);
        scheduleSubscribe();
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data as string) as {
            type?: string;
            status?: LivePriceStatus;
            prices?: LivePriceTick[];
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
        } catch {
          // ignore malformed messages
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        scripsKeyRef.current = "";
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
      if (subscribeTimerRef.current) window.clearTimeout(subscribeTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, refreshStatus, markRefreshing, scheduleSubscribe]);

  useEffect(() => {
    if (!enabled || !connected) return;
    scripsKeyRef.current = "";
    scheduleSubscribe();
  }, [enabled, connected, visibleScrips.join(","), scheduleSubscribe]);

  return { status, connected, refreshing, refreshStatus };
}
