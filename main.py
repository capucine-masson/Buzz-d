import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import deezer
import game
from database import get_connection, init_db
from ws_manager import manager

load_dotenv()

NICKNAME_MAX_LENGTH = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Buzz'd", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _redirect(path: str, **params) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value}
    url = f"{path}?{urlencode(query)}" if query else path
    return RedirectResponse(url=url, status_code=303)


def _clean_nickname(raw: str) -> str | None:
    nickname = raw.strip()
    if not nickname or len(nickname) > NICKNAME_MAX_LENGTH:
        return None
    return nickname


@app.get("/")
async def home(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "home.html", {"error": error})


@app.post("/rooms")
async def create_room(nickname: str = Form(...)):
    clean_nickname = _clean_nickname(nickname)
    if clean_nickname is None:
        return _redirect("/", error="Pseudo invalide (1 à 20 caractères)")

    conn = get_connection()
    try:
        code = game.create_room(conn, clean_nickname)
    finally:
        conn.close()

    return _redirect(f"/room/{code}", player=clean_nickname)


@app.post("/join")
async def join_room(code: str = Form(...), nickname: str = Form(...)):
    room_code = code.strip().upper()
    clean_nickname = _clean_nickname(nickname)

    if not room_code:
        return _redirect("/", error="Code de room requis")
    if clean_nickname is None:
        return _redirect("/", error="Pseudo invalide (1 à 20 caractères)")

    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return _redirect("/", error="Room introuvable")

        try:
            game.add_player(conn, room_code, clean_nickname)
        except sqlite3.IntegrityError:
            conn.close()
            return _redirect(f"/room/{room_code}", error="Ce pseudo est déjà pris dans cette room")

        players = game.list_players(conn, room_code)
    finally:
        conn.close()

    await manager.broadcast(
        room_code, {"type": "players_update", "players": [dict(p) for p in players]}
    )
    return _redirect(f"/room/{room_code}", player=clean_nickname)


@app.get("/room/{code}")
async def room_page(request: Request, code: str, player: str = "", error: str = "", loaded: str = ""):
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return _redirect("/", error="Room introuvable")
        players = game.list_players(conn, room_code)
        track_count = game.count_tracks(conn, room_code)
    finally:
        conn.close()

    is_host = bool(player) and player == room["host_nickname"]
    return templates.TemplateResponse(
        request,
        "room.html",
        {
            "room": room,
            "players": players,
            "player": player,
            "is_host": is_host,
            "track_count": track_count,
            "error": error,
            "loaded": loaded,
        },
    )


@app.post("/rooms/{code}/playlist")
async def set_playlist(code: str, playlist_url: str = Form(...), player: str = Form(...)):
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return _redirect("/", error="Room introuvable")

        if player != room["host_nickname"]:
            conn.close()
            return _redirect(
                f"/room/{room_code}", player=player, error="Seul l'hôte peut importer une playlist"
            )

        try:
            playlist_id = await deezer.resolve_playlist_id(playlist_url)
            tracks = await deezer.fetch_playlist_tracks(playlist_id)
        except deezer.DeezerError as exc:
            conn.close()
            return _redirect(f"/room/{room_code}", player=player, error=str(exc))

        game.save_tracks(conn, room_code, tracks)
        conn.execute("UPDATE rooms SET playlist_url = ? WHERE code = ?", (playlist_url, room_code))
        conn.commit()
    finally:
        conn.close()

    await manager.broadcast(room_code, {"type": "playlist_loaded", "track_count": len(tracks)})
    return _redirect(f"/room/{room_code}", player=player, loaded=str(len(tracks)))


@app.websocket("/ws/{room_code}")
async def room_websocket(websocket: WebSocket, room_code: str):
    code = room_code.strip().upper()
    await manager.connect(code, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(code, websocket)
