"""Субтитры из сегментов Whisper: SRT и WebVTT.

Сегмент — словарь {start, end, text} с временем в секундах от начала файла.
"""


def _stamp(seconds: float, comma=True) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clean(segments):
    """Пустые сегменты выкидываем, а нулевую длительность растягиваем — иначе плеер их не покажет."""
    out = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start") or 0.0)
        end = float(seg.get("end") or 0.0)
        if end <= start:
            end = start + 0.5
        if out and start < out[-1][1]:  # куски файла могут чуть наезжать друг на друга
            start = out[-1][1]
            end = max(end, start + 0.3)
        out.append((start, end, text))
    return out


def to_srt(segments) -> str:
    lines = []
    for i, (start, end, text) in enumerate(_clean(segments), 1):
        lines.append(f"{i}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return "\n".join(lines)


def to_vtt(segments) -> str:
    lines = ["WEBVTT", ""]
    for start, end, text in _clean(segments):
        lines.append(f"{_stamp(start, comma=False)} --> {_stamp(end, comma=False)}\n{text}\n")
    return "\n".join(lines)
