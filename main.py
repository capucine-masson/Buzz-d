import asyncio
import random
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
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
ROUND_TIMEOUT_SECONDS = 32  # un peu plus long qu'un extrait Deezer (30s)
NEXT_ROUND_DELAY_SECONDS = 4  # temps de lire le résultat avant d'enchaîner
HOST_LEAVE_GRACE_SECONDS = 5  # laisse le temps à un simple reload de page de se reconnecter
ROOM_IDLE_TIMEOUT_SECONDS = 15 * 60  # purge une room sans activité depuis plus de 15 min
ROOM_CLEANUP_INTERVAL_SECONDS = 60  # fréquence du passage de ménage

# Une seule tâche "en attente" à la fois par room : soit le minuteur qui force la
# révélation si personne ne buzze, soit le délai avant d'enchaîner sur le morceau
# suivant. Un nouveau départ de manche (manuel ou auto) annule toujours la précédente.
_pending_tasks: dict[str, asyncio.Task] = {}

# Tâche de "grâce" après la déconnexion de l'hôte : si aucune reconnexion de sa
# part n'arrive dans les temps, la room est considérée comme abandonnée.
_host_leave_tasks: dict[str, asyncio.Task] = {}

# Horodatage (perf counter) de la dernière activité par room : une room jamais
# retouchée pendant ROOM_IDLE_TIMEOUT_SECONDS est purgée par _cleanup_idle_rooms,
# playlist/morceaux/joueurs compris (cascade FK), pour ne pas laisser la base
# grossir indéfiniment avec des parties abandonnées.
_room_last_seen: dict[str, float] = {}


def _touch_room(room_code: str) -> None:
    _room_last_seen[room_code] = time.monotonic()


def _cancel_pending_task(room_code: str) -> None:
    task = _pending_tasks.pop(room_code, None)
    if task and not task.done():
        task.cancel()


def _cancel_host_leave_task(room_code: str) -> None:
    task = _host_leave_tasks.pop(room_code, None)
    if task and not task.done():
        task.cancel()


async def _cleanup_idle_rooms() -> None:
    while True:
        await asyncio.sleep(ROOM_CLEANUP_INTERVAL_SECONDS)
        now = time.monotonic()
        stale_codes = [
            code
            for code, last_seen in list(_room_last_seen.items())
            if now - last_seen > ROOM_IDLE_TIMEOUT_SECONDS
        ]
        if not stale_codes:
            continue

        conn = get_connection()
        try:
            conn.executemany(
                "DELETE FROM rooms WHERE code = ?", [(code,) for code in stale_codes]
            )
            conn.commit()
        finally:
            conn.close()

        for code in stale_codes:
            _room_last_seen.pop(code, None)
            _cancel_pending_task(code)
            _cancel_host_leave_task(code)
            rounds.clear(code)


async def _handle_host_disconnect(room_code: str, host_nickname: str) -> None:
    try:
        await asyncio.sleep(HOST_LEAVE_GRACE_SECONDS)
    except asyncio.CancelledError:
        return

    if manager.is_player_connected(room_code, host_nickname):
        return  # reconnecté entre-temps (ex: simple rechargement de page)

    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is not None and room["status"] != "finished":
            game.set_room_status(conn, room_code, "finished")
    finally:
        conn.close()

    _cancel_pending_task(room_code)
    rounds.clear(room_code)
    await manager.broadcast(room_code, {"type": "host_left"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_task = asyncio.create_task(_cleanup_idle_rooms())
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(title="Buzz'd", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _asset_version(relative_path: str) -> int:
    # Sert de cache-buster : force le navigateur à recharger le CSS/JS dès
    # qu'on modifie le fichier, au lieu de garder une version mise en cache.
    return int((Path("static") / relative_path).stat().st_mtime)


templates.env.globals["asset_version"] = _asset_version


def _redirect(path: str, **params) -> RedirectResponse:
    query = {key: value for key, value in params.items() if value}
    url = f"{path}?{urlencode(query)}" if query else path
    return RedirectResponse(url=url, status_code=303)


def _clean_nickname(raw: str) -> str | None:
    nickname = raw.strip()
    if not nickname or len(nickname) > NICKNAME_MAX_LENGTH:
        return None
    return nickname


async def _advance_round(room_code: str) -> None:
    """Pioche le prochain morceau (ou termine la partie) et diffuse aux joueurs.
    Utilisé aussi bien par le bouton hôte que par l'enchaînement automatique."""
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            return

        track = game.get_random_unplayed_track(conn, room_code)
        if track is None:
            game.set_room_status(conn, room_code, "finished")
            ranking = [dict(p) for p in game.final_ranking(conn, room_code)]
            conn.close()
            rounds.clear(room_code)
            await manager.broadcast(room_code, {"type": "game_over", "players": ranking})
            return

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
    _schedule_round_timeout(room_code, track_data["id"])


def _schedule_round_timeout(room_code: str, track_id: int) -> None:
    _cancel_pending_task(room_code)
    _pending_tasks[room_code] = asyncio.create_task(_handle_round_timeout(room_code, track_id))


async def _handle_round_timeout(room_code: str, track_id: int) -> None:
    """Si personne n'a buzzé quand l'extrait est fini, révèle automatiquement (0 pt)
    et enchaîne. N'interrompt jamais un joueur en train de répondre (locked_by set)."""
    try:
        await asyncio.sleep(ROUND_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return

    round_info = rounds.get(room_code)
    if (
        round_info is None
        or round_info["track_id"] != track_id
        or round_info["revealed"]
        or round_info["locked_by"] is not None
    ):
        return

    revealed = await rounds.reveal(room_code, False, False, "", "")
    if revealed is None:
        return

    await manager.broadcast(
        room_code,
        {
            "type": "round_result",
            "title_correct": False,
            "artist_correct": False,
            "points_earned": 0,
            "title": revealed["title"],
            "artist": revealed["artist"],
            "answered_by": None,
        },
    )
    _schedule_next_round(room_code)


def _schedule_next_round(room_code: str) -> None:
    _cancel_pending_task(room_code)
    _pending_tasks[room_code] = asyncio.create_task(_delayed_advance(room_code))


async def _delayed_advance(room_code: str) -> None:
    try:
        await asyncio.sleep(NEXT_ROUND_DELAY_SECONDS)
    except asyncio.CancelledError:
        return
    await _advance_round(room_code)


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
        if game.nickname_active_elsewhere(conn, clean_nickname):
            conn.close()
            return _redirect("/", error="Ce pseudo est déjà utilisé dans une partie en cours")
        code = game.create_room(conn, clean_nickname)
    finally:
        conn.close()

    _touch_room(code)
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

        if game.nickname_active_elsewhere(conn, clean_nickname):
            conn.close()
            return _redirect(
                f"/room/{room_code}", error="Ce pseudo est déjà utilisé dans une partie en cours"
            )

        try:
            game.add_player(conn, room_code, clean_nickname)
        except sqlite3.IntegrityError:
            conn.close()
            return _redirect(f"/room/{room_code}", error="Ce pseudo est déjà pris dans cette room")

        players = game.list_players(conn, room_code)
    finally:
        conn.close()

    _touch_room(room_code)
    await manager.broadcast(
        room_code, {"type": "players_update", "players": [dict(p) for p in players]}
    )
    return _redirect(f"/room/{room_code}", player=clean_nickname)


@app.post("/rooms/{code}/test-player")
async def add_test_player(code: str, player: str = Form(...), nickname: str = Form(...)):
    room_code = code.strip().upper()
    clean_nickname = _clean_nickname(nickname)
    if clean_nickname is None:
        return JSONResponse({"error": "invalid_nickname"}, status_code=400)

    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return JSONResponse({"error": "room_not_found"}, status_code=404)
        if player != room["host_nickname"]:
            conn.close()
            return JSONResponse({"error": "not_host"}, status_code=403)

        if game.nickname_active_elsewhere(conn, clean_nickname):
            conn.close()
            return JSONResponse({"error": "nickname_taken"}, status_code=409)

        try:
            game.add_player(conn, room_code, clean_nickname)
        except sqlite3.IntegrityError:
            conn.close()
            return JSONResponse({"error": "nickname_taken"}, status_code=409)

        players = game.list_players(conn, room_code)
    finally:
        conn.close()

    _touch_room(room_code)
    await manager.broadcast(
        room_code, {"type": "players_update", "players": [dict(p) for p in players]}
    )
    return JSONResponse({"ok": True})


@app.get("/room/{code}")
async def room_page(request: Request, code: str, player: str = "", error: str = ""):
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

    _touch_room(room_code)
    is_host = bool(player) and player == room["host_nickname"]
    round_info = rounds.get(room_code)

    final_ranking = None
    if room["status"] == "finished":
        conn = get_connection()
        try:
            final_ranking = [dict(p) for p in game.final_ranking(conn, room_code)]
        finally:
            conn.close()

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
            "final_ranking": final_ranking,
            "error": error,
        },
    )


@app.get("/demo/{code}")
async def demo_page(request: Request, code: str, count: int = 0, player: str = ""):
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return _redirect("/", error="Room introuvable")
        players = game.list_players(conn, room_code)
    finally:
        conn.close()

    _touch_room(room_code)
    # `count` fixe un nombre minimum de cadrans : on complète avec des écrans
    # vierges (pointant vers l'accueil) si moins de joueurs ont déjà rejoint.
    extra_count = max(0, count - len(players))

    return templates.TemplateResponse(
        request,
        "demo.html",
        {"room": room, "players": players, "extra_count": extra_count, "player": player},
    )


@app.post("/rooms/{code}/playlist")
async def set_playlist(
    code: str,
    playlist_url: str = Form(...),
    player: str = Form(...),
    desired_count: str = Form(""),
    embedded: str = Form(""),
):
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

        # On ne garde en base que le nombre de morceaux réellement demandé (tirés au
        # sort dans la playlist complète) plutôt que d'y stocker des centaines de
        # morceaux qui ne seront jamais joués.
        if desired_count.strip():
            try:
                parsed_count = int(desired_count)
            except ValueError:
                parsed_count = None
            if parsed_count is not None and 0 < parsed_count < len(tracks):
                tracks = random.sample(tracks, parsed_count)

        game.save_tracks(conn, room_code, tracks)
        conn.execute("UPDATE rooms SET playlist_url = ? WHERE code = ?", (playlist_url, room_code))
        conn.commit()
        players_count = len(game.list_players(conn, room_code))
    finally:
        conn.close()

    _touch_room(room_code)
    # L'import lance directement la partie (plus d'étape intermédiaire "Playlist prête").
    # Pas de broadcast "playlist_loaded" ici : round_start (dans _advance_round) suffit
    # à faire basculer l'UI de tout le monde, et un message qui forcerait une navigation
    # ici entrerait en course avec la redirection HTTP de l'hôte vers /demo.
    _cancel_pending_task(room_code)
    await _advance_round(room_code)

    # Un cadran déjà embarqué dans la vue démo ne doit jamais y rerediriger :
    # ça imbriquerait une grille démo entière dans son propre écran.
    if embedded:
        return _redirect(f"/room/{room_code}", player=player)
    return _redirect(f"/demo/{room_code}", count=players_count, player=player)


@app.post("/rooms/{code}/start")
async def start_round(code: str, player: str = Form(...), embedded: str = Form("")):
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            return _redirect("/", error="Room introuvable")

        if player != room["host_nickname"]:
            return _redirect(
                f"/room/{room_code}", player=player, error="Seul l'hôte peut lancer la manche"
            )

        players_count = len(game.list_players(conn, room_code))
    finally:
        conn.close()

    _touch_room(room_code)
    # Un déclenchement manuel de l'hôte (premier morceau ou "morceau suivant" pour
    # sauter l'attente) prime toujours sur un enchaînement automatique en cours.
    _cancel_pending_task(room_code)
    await _advance_round(room_code)

    # L'hôte est renvoyé vers la vue démo multi-téléphones après un lancement -
    # sauf si l'action venait déjà d'un cadran embarqué dans cette vue, auquel
    # cas rerediriger vers /demo imbriquerait une grille démo dans son écran.
    if embedded:
        return _redirect(f"/room/{room_code}", player=player)
    return _redirect(f"/demo/{room_code}", count=players_count, player=player)


@app.post("/rooms/{code}/playback")
async def set_playback(code: str, player: str = Form(...), action: str = Form(...)):
    room_code = code.strip().upper()
    if action not in ("play", "pause"):
        return JSONResponse({"error": "invalid_action"}, status_code=400)

    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            conn.close()
            return JSONResponse({"error": "room_not_found"}, status_code=404)
        if player != room["host_nickname"]:
            conn.close()
            return JSONResponse({"error": "not_host"}, status_code=403)
    finally:
        conn.close()

    _touch_room(room_code)
    await manager.broadcast(room_code, {"type": "playback", "action": action})
    return JSONResponse({"ok": True})


@app.post("/rooms/{code}/buzz")
async def buzz(code: str, player: str = Form(...)):
    room_code = code.strip().upper()

    conn = get_connection()
    try:
        if not game.player_exists(conn, room_code, player):
            return JSONResponse({"locked": False, "reason": "unknown_player"}, status_code=403)
    finally:
        conn.close()

    _touch_room(room_code)
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

    _touch_room(room_code)

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

    _schedule_next_round(room_code)

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


@app.post("/rooms/{code}/replay")
async def replay_room(code: str, player: str = Form(...), embedded: str = Form("")):
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        if room is None:
            return _redirect("/", error="Room introuvable")

        if player != room["host_nickname"]:
            return _redirect(
                f"/room/{room_code}", player=player, error="Seul l'hôte peut relancer la partie"
            )

        # Retire un nouveau tirage dans la playlist d'origine plutôt que de rejouer
        # systématiquement les mêmes morceaux que la partie précédente.
        previous_count = game.count_tracks(conn, room_code)
        if room["playlist_url"]:
            try:
                playlist_id = await deezer.resolve_playlist_id(room["playlist_url"])
                tracks = await deezer.fetch_playlist_tracks(playlist_id)
            except deezer.DeezerError:
                tracks = None
            if tracks:
                if 0 < previous_count < len(tracks):
                    tracks = random.sample(tracks, previous_count)
                game.save_tracks(conn, room_code, tracks)

        game.reset_for_replay(conn, room_code)
        players = game.list_players(conn, room_code)
        players_count = len(players)
    finally:
        conn.close()

    _touch_room(room_code)
    _cancel_pending_task(room_code)
    await manager.broadcast(
        room_code, {"type": "players_update", "players": [dict(p) for p in players]}
    )
    await _advance_round(room_code)

    if embedded:
        return _redirect(f"/room/{room_code}", player=player)
    return _redirect(f"/demo/{room_code}", count=players_count, player=player)


@app.post("/rooms/{code}/propose-replay")
async def propose_replay(code: str, player: str = Form(...)):
    room_code = code.strip().upper()

    conn = get_connection()
    try:
        if not game.player_exists(conn, room_code, player):
            return JSONResponse({"error": "unknown_player"}, status_code=403)
    finally:
        conn.close()

    _touch_room(room_code)
    await manager.broadcast(room_code, {"type": "replay_proposed", "by": player})
    return JSONResponse({"ok": True})


@app.post("/rooms/{code}/leave")
async def leave_room(code: str, player: str = Form(...)):
    """Départ explicite (clic sur le logo ou "Retour à l'accueil"). Contrairement à
    la détection par déconnexion WebSocket, ceci est un signal volontaire et sans
    ambiguïté : pas de délai de grâce, la room se termine immédiatement si c'est
    l'hôte qui part."""
    room_code = code.strip().upper()
    conn = get_connection()
    try:
        room = game.get_room(conn, room_code)
        is_host = room is not None and player == room["host_nickname"]
        if is_host and room["status"] != "finished":
            game.set_room_status(conn, room_code, "finished")
    finally:
        conn.close()

    if is_host:
        _cancel_pending_task(room_code)
        _cancel_host_leave_task(room_code)
        rounds.clear(room_code)
        await manager.broadcast(room_code, {"type": "host_left"})

    return _redirect("/")


@app.websocket("/ws/{room_code}")
async def room_websocket(websocket: WebSocket, room_code: str, player: str = ""):
    code = room_code.strip().upper()
    clean_player = player.strip()
    _touch_room(code)
    await manager.connect(code, clean_player, websocket)

    conn = get_connection()
    try:
        room = game.get_room(conn, code)
    finally:
        conn.close()
    is_host = bool(room) and clean_player == room["host_nickname"]

    if is_host:
        # Une reconnexion de l'hôte (ex: reload de page) annule un éventuel
        # compte à rebours de départ programmé lors d'une déconnexion précédente.
        _cancel_host_leave_task(code)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(code, websocket)
        if is_host:
            _host_leave_tasks[code] = asyncio.create_task(
                _handle_host_disconnect(code, clean_player)
            )
