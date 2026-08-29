"""Разбор текста: поиск слова-триггера и подсчёт слов."""
import re
from difflib import SequenceMatcher

WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*|\d+", re.UNICODE)
# Запас на то, что Whisper слышит "Алиса" как "Алис"/"Алисо"/"Ализа".
# 0.82 отбрасывал даже "алисо" (ratio ровно 0.80) — слово-триггер регулярно пролетал мимо.
SIMILARITY = 0.75


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def find_wake_word(text: str, aliases):
    """(нашли?, остаток фразы после слова-триггера).

    Слово-триггер ищется по всей фразе, чтобы "Окей, Алиса, включи свет"
    тоже срабатывало, а хвост после него сразу считался командой.
    """
    if not text:
        return False, ""
    variants = [a.lower() for a in aliases if a]
    for m in WORD_RE.finditer(text.lower()):
        token = m.group()
        if any(_similar(token, v) for v in variants):
            return True, text[m.end():].lstrip(" ,.!?;:—-–").strip()
    return False, ""


def strip_trigger(text: str, triggers):
    """Если текст начинается с одного из триггеров (нечётко) — остаток, иначе None.

    Нужно агенту: «сделай открой блокнот» -> «открой блокнот». Сравнение нечёткое,
    потому что Whisper может услышать «сделай» как «сделаи»/«зделай».
    """
    if not text:
        return None
    variants = [t.lower() for t in triggers if t]
    m = WORD_RE.search(text)
    if not m:
        return None
    token = m.group().lower()
    if any(_similar(token, v) for v in variants):
        return text[m.end():].lstrip(" ,.!?;:—-–").strip()
    return None


def matches_phrase(text: str, phrase: str) -> bool:
    """Нечёткое сравнение целой фразы (стоп-слово, подтверждение) с услышанным."""
    if not text or not phrase:
        return False
    heard = WORD_RE.findall(text.lower())
    wanted = WORD_RE.findall(phrase.lower())
    if not heard or not wanted:
        return False
    if len(wanted) == 1:
        return any(_similar(tok, wanted[0]) for tok in heard)
    # все слова фразы должны встретиться по порядку
    it = iter(heard)
    return all(any(_similar(tok, w) for tok in it) for w in wanted)


def _similar(token: str, variant: str) -> bool:
    if token == variant:
        return True
    if abs(len(token) - len(variant)) > 2:
        return False
    return SequenceMatcher(None, token, variant).ratio() >= SIMILARITY
