import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import deezer
import game
import groq_client
import validation
from database import get_connection, init_db
from round_state import rounds
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
        played, total = game.progress(conn, room_code)
    finally:
        conn.close()

    is_host = bool(player) and player == room["host_nickname"]
    round_info = rounds.get(room_code)
    return templates.TemplateResponse(
        request,
        "room.html",
        {
            "room": room,
            "players": players,
            "player": player,
            "is_host": is_host,
            "track_count": track_count,
            "played": played,
            "total": total,
            "round_info": round_info,
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


@app.post("/rooms/{code}/start")
async def start_round(code: str, player: str = Form(...)):
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
                f"/room/{room_code}", player=player, error="Seul l'hôte peut lancer la manche"
            )

        track = game.get_random_unplayed_track(conn, room_code)
        if track is None:
            game.set_room_status(conn, room_code, "finished")
            conn.close()
            rounds.clear(room_code)
            await manager.broadcast(room_code, {"type": "game_over"})
            return _redirect(f"/room/{room_code}", player=player)

        game.mark_track_played(conn, track["id"])
        game.set_room_status(conn, room_code, "playing")
        played, total = game.progress(conn, room_code)
        track_data = dict(track)
    finally:
        conn.close()

    rounds.start_round(room_code, track_data)
    await manager.broadcast(
        room_code,
        {
            "type": "round_start",
            "preview_url": track_data["preview_url"],
            "played": played,
            "total": total,
        },
    )
    return _redirect(f"/room/{room_code}", player=player)


@app.post("/rooms/{code}/buzz")
async def buzz(code: str, player: str = Form(...)):
    room_code = code.strip().upper()

    conn = get_connection()
    try:
        if not game.player_exists(conn, room_code, player):
            return JSONResponse({"locked": False, "reason": "unknown_player"}, status_code=403)
    finally:
        conn.close()

    locked = await rounds.try_buzz(room_code, player)
    if locked:
        await manager.broadcast(room_code, {"type": "buzz_locked", "locked_by": player})
    return JSONResponse({"locked": locked})


@app.post("/rooms/{code}/answer")
async def submit_answer(
    code: str,
    player: str = Form(...),
    title_answer: str = Form(""),
    artist_answer: str = Form(""),
):
    room_code = code.strip().upper()
    round_info = rounds.get(room_code)

    if round_info is None or round_info["locked_by"] != player or round_info["revealed"]:
        return JSONResponse({"error": "invalid_state"}, status_code=409)

    title = round_info["title"]
    artist = round_info["artist"]

    title_correct = validation.exact_match_single(title_answer, title)
    if not title_correct and title_answer.strip():
        title_correct = await groq_client.judge_field(title_answer, "title", title, artist)

    artist_correct = validation.exact_match_single(artist_answer, artist)
    if not artist_correct and artist_answer.strip():
        artist_correct = await groq_client.judge_field(artist_answer, "artist", title, artist)

    revealed = await rounds.reveal(room_code, title_correct, artist_correct, title_answer, artist_answer)
    if revealed is None:
        return JSONResponse({"error": "already_revealed"}, status_code=409)

    points_earned = int(title_correct) + int(artist_correct)

    if points_earned > 0:
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE players SET score = score + ? WHERE room_code = ? AND nickname = ?",
                (points_earned, room_code, player),
            )
            conn.commit()
            players = game.list_players(conn, room_code)
        finally:
            conn.close()
        await manager.broadcast(
            room_code, {"type": "players_update", "players": [dict(p) for p in players]}
        )

    await manager.broadcast(
        room_code,
        {
            "type": "round_result",
            "title_correct": title_correct,
            "artist_correct": artist_correct,
            "points_earned": points_earned,
            "title": title,
            "artist": artist,
            "answered_by": player,
        },
    )
    return JSONResponse(
        {"title_correct": title_correct, "artist_correct": artist_correct, "points_earned": points_earned}
    )


@app.websocket("/ws/{room_code}")
async def room_websocket(websocket: WebSocket, room_code: str):
    code = room_code.strip().upper()
    await manager.connect(code, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(code, websocket)
