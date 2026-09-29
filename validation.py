import re
import unicodedata

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Minuscule, sans accents, sans ponctuation ni espaces superflus."""
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    without_punctuation = _NON_ALNUM_RE.sub(" ", without_accents)
    return _WHITESPACE_RE.sub(" ", without_punctuation).strip()


def exact_match(guess: str, title: str, artist: str) -> bool:
    normalized_guess = normalize(guess)
    if not normalized_guess:
        return False
    return normalized_guess == normalize(title) or normalized_guess == normalize(artist)
