import asyncio


class RoundManager:
    """État de la manche en cours par room, en mémoire (un seul process uvicorn)."""

    def __init__(self) -> None:
        self._rounds: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, room_code: str) -> asyncio.Lock:
        return self._locks.setdefault(room_code, asyncio.Lock())

    def start_round(self, room_code: str, track: dict) -> None:
        self._rounds[room_code] = {
            "track_id": track["id"],
            "title": track["title"],
            "artist": track["artist"],
            "preview_url": track["preview_url"],
            "locked_by": None,
            "revealed": False,
            "correct": None,
            "answer_text": None,
        }

    def get(self, room_code: str) -> dict | None:
        return self._rounds.get(room_code)

    async def try_buzz(self, room_code: str, nickname: str) -> bool:
        """Verrouille le buzzer pour ce joueur si personne d'autre n'a buzzé. Atomique par room."""
        async with self._lock(room_code):
            round_ = self._rounds.get(room_code)
            if round_ is None or round_["locked_by"] is not None or round_["revealed"]:
                return False
            round_["locked_by"] = nickname
            return True

    async def reveal(self, room_code: str, correct: bool, answer_text: str) -> dict | None:
        """Marque la manche comme révélée. Retourne None si déjà révélée (protège du double traitement)."""
        async with self._lock(room_code):
            round_ = self._rounds.get(room_code)
            if round_ is None or round_["revealed"]:
                return None
            round_["revealed"] = True
            round_["correct"] = correct
            round_["answer_text"] = answer_text
            return round_

    def clear(self, room_code: str) -> None:
        self._rounds.pop(room_code, None)
        self._locks.pop(room_code, None)


rounds = RoundManager()
