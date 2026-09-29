import json
import os

import httpx

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "openai/gpt-oss-20b"
_TIMEOUT = 8.0

_SYSTEM_PROMPT = "Tu juges des réponses de blind test musical. Réponds uniquement en JSON strict."


async def judge_field(guess: str, field: str, title: str, artist: str) -> bool:
    """Demande à Groq si la réponse est équivalente au titre OU à l'artiste réel (selon `field`).
    Échoue "fermé" (False) en cas de souci (pas de clé, timeout, erreur réseau)."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return False

    field_label = "titre" if field == "title" else "artiste"
    target = title if field == "title" else artist

    user_prompt = (
        f'Dans un blind test musical, le morceau joué est "{title}" par "{artist}".\n'
        f'Le joueur devait deviner le {field_label}, sa réponse pour ce champ est : "{guess}"\n'
        f'Le vrai {field_label} est : "{target}"\n'
        "Est-ce une réponse équivalente (faute de frappe, surnom d'artiste, orthographe "
        "approximative, article/featuring omis) ? "
        'Réponds uniquement par ce JSON : {"correct": true} ou {"correct": false}.'
    )

    payload = {
        "model": _GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(_GROQ_URL, json=payload, headers=headers)
    except httpx.RequestError:
        return False

    if response.status_code != 200:
        return False

    try:
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return bool(parsed.get("correct", False))
    except (KeyError, IndexError, ValueError):
        return False
