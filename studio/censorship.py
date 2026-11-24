"""Hide listed words without changing the original transcript."""
import re
import unicodedata
from functools import lru_cache

TOKEN = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*", re.UNICODE)
# The vowels of a censored word become these symbols, so the word stays readable.
LEET = {"a": "@", "e": "3", "i": "1", "o": "*", "u": "#"}


def _key(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c)).replace("’", "'")


def normalize_words(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Enter the words to censor as text.")
    if len(value) > 10000:
        raise ValueError("The censorship list can have up to 10000 characters.")
    words, seen = [], set()
    for word in re.split(r"[,;\r\n]+", value):
        word = unicodedata.normalize("NFC", word.strip())
        if not word:
            continue
        if len(word) > 60 or not TOKEN.fullmatch(word):
            raise ValueError("Enter up to 500 individual words, each with at most 60 characters.")
        key = _key(word)
        if key not in seen:
            seen.add(key)
            words.append(word)
    if len(words) > 500:
        raise ValueError("Enter up to 500 individual words, each with at most 60 characters.")
    return "\n".join(words)


@lru_cache(maxsize=64)
def _listed(words):
    return frozenset(_key(word) for word in words.splitlines() if word)


def _mask_word(word: str) -> str:
    """Swap the vowels for symbols, keeping the consonants as they were."""
    masked = "".join(LEET.get(_key(char), char) for char in word)
    # A word without vowels would stay readable, so hide it fully instead.
    return masked if masked != word else "*" * len(word)


def mask_text(text: str, words: str) -> str:
    listed = _listed(words)
    return TOKEN.sub(lambda m: _mask_word(m[0]) if _key(m[0]) in listed else m[0],
                     unicodedata.normalize("NFC", text))


def mute_ranges(speech: list, words: str, start: float, end: float) -> list[tuple]:
    """Speech times become clip times, with a small margin either side of the word."""
    listed, ranges = _listed(words), []
    for word in speech:
        if not any(_key(m[0]) in listed for m in TOKEN.finditer(unicodedata.normalize("NFC", word["w"]))):
            continue
        if word["e"] <= start or word["s"] >= end:
            continue
        low = max(start, word.get("cut_start", start), word["s"] - .08) - start
        high = min(end, word.get("cut", end), word["e"] + .08) - start
        if high > low:
            ranges.append((low, high))
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def audio_filter(ranges: list) -> str:
    filters = "loudnorm=I=-14:TP=-1.5:LRA=11"
    if ranges:
        enable = "+".join(f"between(t,{low:.3f},{high:.3f})" for low, high in ranges)
        # loudnorm can return a whole short clip in one frame. Evaluate each sample
        # so a word in the middle is still silenced, whatever the frame size.
        filters += f",aeval=exprs='val(ch)*not({enable})':c=same"
    return filters
