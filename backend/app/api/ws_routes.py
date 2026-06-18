import asyncio
import contextlib
import queue

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.schemas import LivePriceScripsIn
from app.services.live_price_service import live_price_service

router = APIRouter()


def _queue_get_with_timeout(tick_queue: queue.Queue, timeout: float):
    try:
        return tick_queue.get(block=True, timeout=timeout)
    except queue.Empty:
        return None


@router.get("/live-prices/status")
def live_price_status() -> dict:
    return live_price_service.status.to_dict()


@router.post("/live-prices/start")
def live_price_start() -> dict:
    live_price_service.start()
    return live_price_service.status.to_dict()


@router.post("/live-prices/stop")
def live_price_stop() -> dict:
    live_price_service.stop()
    return live_price_service.status.to_dict()


@router.post("/live-prices/watch")
def live_price_watch(body: LivePriceScripsIn) -> dict:
    count = live_price_service.set_watch_scrips(body.scrips)
    if not live_price_service.status.running:
        live_price_service.start()
    return {"watch_count": count, "status": live_price_service.status.to_dict()}


@router.post("/live-prices/refresh")
def live_price_refresh(body: LivePriceScripsIn) -> dict:
    if not live_price_service.status.running:
        live_price_service.start()
    return live_price_service.refresh_by_scrips(body.scrips)


@router.get("/live-prices/latest")
def live_price_latest(scrips: str | None = None) -> dict:
    scrip_list = [s.strip() for s in scrips.split(",")] if scrips else None
    rows = live_price_service.get_latest_prices(scrip_list)
    return {"count": len(rows), "prices": rows}


@router.websocket("/ws/live-prices")
async def live_prices_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    connection_id = id(websocket)
    tick_queue: queue.Queue = queue.Queue(maxsize=500)
    live_price_service.register_ws_queue(tick_queue)

    if not live_price_service.status.running:
        live_price_service.start()

    async def send_json_safe(payload: dict) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except WebSocketDisconnect:
            return False
        except RuntimeError:
            # Socket already closed
            return False
        except Exception:
            return False

    async def receive_client_messages() -> None:
        try:
            while True:
                data = await websocket.receive_json()
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "subscribe":
                    scrips = data.get("scrips") or []
                    if isinstance(scrips, list):
                        count = live_price_service.set_connection_watch(
                            connection_id,
                            [str(s) for s in scrips],
                        )
                        await send_json_safe(
                            {
                                "type": "subscribed",
                                "watch_count": count,
                                "status": live_price_service.status.to_dict(),
                            }
                        )
        except WebSocketDisconnect:
            pass
        except RuntimeError:
            pass

    receive_task = asyncio.create_task(receive_client_messages())

    try:
        if not await send_json_safe(
            {"type": "connected", "status": live_price_service.status.to_dict()}
        ):
            return

        while True:
            payload = await asyncio.to_thread(_queue_get_with_timeout, tick_queue, 30.0)
            if payload is None:
                if not await send_json_safe(
                    {
                        "type": "heartbeat",
                        "status": live_price_service.status.to_dict(),
                    }
                ):
                    break
            elif not await send_json_safe(payload):
                break
    except WebSocketDisconnect:
        pass
    finally:
        receive_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receive_task
        live_price_service.remove_connection(connection_id)
        live_price_service.unregister_ws_queue(tick_queue)
