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


def _similar(token: str, variant: str) -> bool:
    if token == variant:
        return True
    if abs(len(token) - len(variant)) > 2:
        return False
    return SequenceMatcher(None, token, variant).ratio() >= SIMILARITY
