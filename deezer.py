import re
from urllib.parse import urlparse

import httpx

_PLAYLIST_PATH_RE = re.compile(r"/playlist/(\d+)")
_REQUEST_TIMEOUT = 10.0

# Domaines Deezer autorisés à être suivis en cas de lien court (évite le SSRF sur une URL arbitraire).
_ALLOWED_HOSTS = {
    "link.deezer.com",
    "dzr.page.link",
    "www.deezer.com",
    "deezer.com",
}


class DeezerError(Exception):
    """Raised when a Deezer playlist can't be resolved (bad URL, private, unreachable)."""


def _extract_from_path(url: str) -> str | None:
    match = _PLAYLIST_PATH_RE.search(url)
    return match.group(1) if match else None


async def resolve_playlist_id(url: str) -> str:
    """Extrait l'ID de playlist depuis une URL Deezer complète ou un lien court (link.deezer.com)."""
    cleaned = url.strip()

    playlist_id = _extract_from_path(cleaned)
    if playlist_id:
        return playlist_id

    host = urlparse(cleaned).netloc.lower()
    if host not in _ALLOWED_HOSTS:
        raise DeezerError("URL de playlist invalide")

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(cleaned)
    except httpx.RequestError as exc:
        raise DeezerError("Impossible de résoudre ce lien Deezer") from exc

    playlist_id = _extract_from_path(str(response.url))
    if not playlist_id:
        raise DeezerError("URL de playlist invalide")

    return playlist_id


async def fetch_playlist_tracks(playlist_id: str) -> list[dict]:
    api_url = f"https://api.deezer.com/playlist/{playlist_id}/tracks"
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(api_url)
    except httpx.RequestError as exc:
        raise DeezerError("Impossible de contacter Deezer, réessaie plus tard") from exc

    if response.status_code != 200:
        raise DeezerError("Playlist introuvable ou privée")

    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        raise DeezerError("Playlist introuvable ou privée")

    tracks = []
    for item in payload.get("data", []):
        title = item.get("title")
        artist = (item.get("artist") or {}).get("name")
        preview_url = item.get("preview")
        if not title or not artist or not preview_url:
            continue
        tracks.append(
            {
                "deezer_track_id": item.get("id"),
                "title": title,
                "artist": artist,
                "preview_url": preview_url,
            }
        )

    if not tracks:
        raise DeezerError("Playlist introuvable, privée, ou sans extrait disponible")

    return tracks
