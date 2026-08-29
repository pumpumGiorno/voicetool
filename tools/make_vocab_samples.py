"""Синтезировать тестовые фразы с именами и редкими словами (Windows SAPI).

Нужен набор, на котором видно пользу словаря подсказок: имена, фамилии, названия
библиотек — то, чего Whisper почти не встречал. Диктовать их вручную каждый раз
неудобно, поэтому фразы наговаривает системный синтезатор Windows.

    python tools/make_vocab_samples.py [папка]

Голос синтезатора звучит ровнее живой речи, поэтому абсолютные цифры точности тут
оптимистичнее реальных — но сравнение «со словарём против без словаря» честное:
обе прогонки идут по одному и тому же звуку.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# (имя файла, что произносится, какие слова обязаны появиться в тексте)
PHRASES = [
    ("v01", "Напиши Кузьминой, что встреча переносится",
     ["Кузьминой"]),
    ("v02", "Спроси у Ивандара про отчёт",
     ["Ивандара"]),
    ("v03", "Открой проект на PySide и запусти сборку",
     ["PySide"]),
    ("v04", "Добавь в задачу библиотеку ctranslate",
     ["ctranslate"]),
    ("v05", "Позвони Анастасии Кузьминой сегодня вечером",
     ["Анастасии", "Кузьминой"]),
    ("v06", "Передай Гильмутдинову документы по проекту",
     ["Гильмутдинову"]),
    ("v07", "Нужно обновить faster whisper до новой версии",
     ["whisper"]),
    ("v08", "Запиши: Хабибуллин просил перезвонить",
     ["Хабибуллин"]),
    ("v09", "Сохрани файл в формате вебвиттиэй",
     ["вебвиттиэй"]),
    ("v10", "Напомни Севастьянову про совещание в пятницу",
     ["Севастьянову"]),
]

SCRIPT = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq 'ru-RU' }} |
         Select-Object -First 1
if ($voice) {{ $s.SelectVoice($voice.VoiceInfo.Name) }}
$s.Rate = 0
$s.SetOutputToWaveFile('{path}')
$s.Speak('{text}')
$s.Dispose()
"""


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "vocab"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, text, expected in PHRASES:
        wav = out / f"{name}.wav"
        # апострофы в PowerShell экранируются удвоением
        script = SCRIPT.format(path=str(wav).replace("'", "''"), text=text.replace("'", "''"))
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                              capture_output=True, text=True)
        if proc.returncode or not wav.exists():
            print(f"  [ошибка] {name}: {proc.stderr.strip()[:120]}")
            continue
        print(f"  {wav.name}  {wav.stat().st_size / 1024:5.0f} КБ  {text}")
        manifest.append({"file": wav.name, "text": text, "expected": expected})

    index = out / "phrases.json"
    import json

    index.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(manifest)} фраз, список: {index}")
    return 0 if manifest else 1


if __name__ == "__main__":
    sys.exit(main())
