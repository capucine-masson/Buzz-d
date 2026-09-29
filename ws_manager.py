import json
from fastapi import WebSocket


class RoomConnectionManager:
    """Tracks active WebSocket connections per room code and broadcasts JSON messages.
    Also remembers which player owns each connection, so the app can detect when a
    specific player (e.g. the host) has no more open connection to a room."""

    def __init__(self) -> None:
        self._rooms: dict[str, list[tuple[WebSocket, str]]] = {}

    async def connect(self, room_code: str, player: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._rooms.setdefault(room_code, []).append((websocket, player))

    def disconnect(self, room_code: str, websocket: WebSocket) -> str | None:
        connections = self._rooms.get(room_code)
        if not connections:
            return None

        player = None
        for index, (ws, ws_player) in enumerate(connections):
            if ws is websocket:
                player = ws_player
                connections.pop(index)
                break

        if not connections:
            self._rooms.pop(room_code, None)
        return player

    def is_player_connected(self, room_code: str, player: str) -> bool:
        connections = self._rooms.get(room_code, [])
        return any(ws_player == player for _, ws_player in connections)

    async def broadcast(self, room_code: str, message: dict) -> None:
        connections = self._rooms.get(room_code, [])
        payload = json.dumps(message)
        for websocket, _player in list(connections):
            try:
                await websocket.send_text(payload)
            except Exception:
                self.disconnect(room_code, websocket)


manager = RoomConnectionManager()
