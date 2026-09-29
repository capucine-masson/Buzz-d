import json
from fastapi import WebSocket


class RoomConnectionManager:
    """Tracks active WebSocket connections per room code and broadcasts JSON messages."""

    def __init__(self) -> None:
        self._rooms: dict[str, list[WebSocket]] = {}

    async def connect(self, room_code: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(room_code, []).append(websocket)

    def disconnect(self, room_code: str, websocket: WebSocket) -> None:
        connections = self._rooms.get(room_code)
        if not connections:
            return
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self._rooms.pop(room_code, None)

    async def broadcast(self, room_code: str, message: dict) -> None:
        connections = self._rooms.get(room_code, [])
        payload = json.dumps(message)
        for connection in list(connections):
            try:
                await connection.send_text(payload)
            except Exception:
                self.disconnect(room_code, connection)


manager = RoomConnectionManager()
