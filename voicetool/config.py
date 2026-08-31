"""Конфигурация: значения по умолчанию здесь, пользовательские — в config.json.

Файл лежит в папке проекта при запуске из исходников и в папке данных пользователя
у собранного exe (см. paths.config_path) — чтобы обновление VoiceTool.exe не стирало настройки.
"""
import json
import logging
import os
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

DEFAULTS = {
    # --- живой режим ---
    "wake_word": "алиса",
    # варианты, как Whisper может расслышать слово-триггер (сравнение нечёткое)
    "wake_word_aliases": ["алиса", "алис", "алисо", "ализа", "лиса", "алеся",
                          "alisa", "alice", "aliza", "elisa"],
    "silence_seconds": 2.5,        # пауза, после которой команда считается законченной
    "wake_silence_seconds": 0.7,   # пауза для короткой фразы со словом-триггером
    "wake_max_seconds": 5.0,       # wake-фраза длиннее не бывает; ограничение экономит CPU
    "max_utterance_seconds": 60,
    "min_speech_seconds": 0.3,     # короче — считаем шумом, не распознаём

    # --- микрофон ---
    "sample_rate": 16000,
    "input_device": None,          # None = устройство по умолчанию, иначе номер из `check`
    "energy_threshold": 0,         # 0 = замерить фоновый шум при старте
    "noise_multiplier": 3.0,       # во сколько раз речь громче фона (крутилка чувствительности)
    "min_energy": 0.005,           # нижняя граница порога, чтобы не ловить тишину
    "calibration_seconds": 1.0,

    # --- распознавание ---
    "model": "small",              # tiny | base | small | medium | large-v3
    # Слово-триггер ловит отдельная лёгкая модель: на нём важна скорость, а не точность
    # (одно слово, дальше всё равно нечёткое сравнение). Это главный вклад в задержку.
    "wake_model": "tiny",          # "" = той же моделью, что и команды
    "wake_beam_size": 1,           # жадный поиск: перебор лучей ради одного слова не нужен
    "device": "auto",              # auto (видеокарта, если есть) | cpu | cuda
    "compute_type": "auto",        # auto = float16 на GPU, int8 на CPU
    # На фрагменте в секунду автоопределение языка часто врёт, поэтому язык живого
    # режима задаётся здесь. null — определять автоматически.
    "language_hint": "ru",
    "beam_size": 2,                # 2 почти не уступает 5 по точности, но заметно быстрее на CPU
    "cpu_threads": 0,              # 0 = все ядра процессора; >0 — ровно столько потоков
    "chunk_seconds": 300,          # длина куска при разборе файла (память не растёт с длиной)
    "whisper_models_dir": "",      # "" = кэш HuggingFace в профиле пользователя

    # --- перевод (файловый режим) ---
    "translator": "argos",         # argos (офлайн) | none (только оригинал)
    "translate_to": "ru",
    "translate_offline_only": False,  # True = не скачивать модели перевода (только уже локальные)

    # --- словарь имён и редких слов (vocabulary.txt в папке данных) ---
    "use_vocabulary": True,

    # --- вывод распознанного текста ---
    "output_mode": "insert",       # insert | show | insert_show
    "press_enter": False,          # ВЫКЛЮЧЕНО по умолчанию: отправлять решает пользователь
    "insert_into_wake_window": True,  # вводить в окно, активное в момент «Алисы»
    # Пауза между символами при наборе, мс. 10 — держат даже медленные поля
    # (Блокнот Windows 11). Меньше — быстрее, но часть приложений начнёт терять символы.
    "type_delay_ms": 15,

    # --- локальный desktop agent (Stage 1) ---
    # smart: точные desktop-команды идут через deterministic fast path, обычный
    # текст остаётся диктовкой, неоднозначные action-like фразы проверяет Ollama.
    "agent_enabled": True,
    "voice_mode": "smart",       # smart | dictation | agent
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3.5:9b",
    "ollama_context": 8192,
    "ollama_temperature": 0.1,
    "ollama_timeout_seconds": 90,
    "ollama_keep_alive": "10m",
    "max_agent_steps": 8,
    "strict_local_ai": True,
    "vision_enabled": True,
    "max_vision_steps": 3,
    "visual_settle_seconds": 0.35,
    "app_launch_timeout_seconds": 5.0,
    "steam_launch_timeout_seconds": 20.0,
    "app_resolver_cache_seconds": 180,
    "allowed_url_schemes": [],   # custom schemes; http/https are always allowed
    "confirmation_timeout_seconds": 120.0,

    # --- интерфейс ---
    "show_floating_widget": True,
    "minimize_to_tray": True,
    "show_notifications": True,
    "start_with_windows": False,   # намеренно выключено: автозапуск включает пользователь
    "start_listening_on_launch": False,
    "hotkey": "Ctrl+Alt+A",
    "hotkey_enabled": True,

    # --- данные ---
    "data_dir": "auto",            # auto = %APPDATA%\\VoiceTool; счётчик и логи вне папки программы
    "log_transcripts": True,
}

# ключи прежних версий: молча игнорируем, чтобы старый config.json не сыпал предупреждениями.
# restore_clipboard — со времён, когда текст вставлялся через буфер обмена; теперь символы
# набираются напрямую и буфер не используется вообще.
OBSOLETE = {"restore_clipboard"}

# Ключи, у которых сменилось значение по умолчанию. Если в файле лежит ровно старое
# умолчание, пользователь его не выбирал — он просто получил его когда-то при создании
# файла. Такие значения обновляем на новые, осознанно выставленные не трогаем.
MIGRATED_DEFAULTS = {
    "wake_model": ("", "tiny"),        # лёгкая модель на слово-триггер — главный выигрыш в задержке
    "compute_type": ("int8", "auto"),  # auto = float16 на видеокарте, int8 на процессоре
    "beam_size": (5, 2),               # beam=2 почти так же точен, но заметно быстрее на CPU
    "noise_multiplier": (4.0, 3.0),    # порог 4× пропускал негромкую речь мимо детектора
    "min_energy": (0.006, 0.005),
    "wake_word_aliases": (["алиса", "алис", "alisa", "alice"],
                          DEFAULTS["wake_word_aliases"]),  # больше вариантов ослышек Whisper
}


class Config(dict):
    __getattr__ = dict.__getitem__

    @property
    def data_dir(self) -> Path:
        return paths.resolve_data_dir(self["data_dir"])

    @property
    def models_dir(self):
        """None = кэш HuggingFace по умолчанию (тоже в профиле пользователя, переживает обновление)."""
        value = self.get("whisper_models_dir") or ""
        if not value:
            return None
        path = Path(os.path.expanduser(value))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save(self, path=None):
        """Атомарная запись: недописанный config.json хуже, чем старый."""
        path = Path(path or self.get("_path") or paths.config_path(self.data_dir))
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {k: v for k, v in self.items() if k in DEFAULTS}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        log.info("Настройки сохранены: %s", path)
        return path


def default_path() -> Path:
    return paths.config_path(paths.resolve_data_dir("auto"))


def load(path: Path = None) -> Config:
    """Читает config.json, дополняя недостающие ключи значениями по умолчанию."""
    path = Path(path) if path else default_path()
    cfg = dict(DEFAULTS)
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"Ошибка в {path}: {e}\nПочините файл или удалите его — он ��оздастся заново.")
        unknown = set(user) - set(DEFAULTS) - OBSOLETE
        if unknown:
            log.warning("Неизвестные ключи в %s игнорируются: %s", path, ", ".join(sorted(unknown)))
        cfg.update({k: v for k, v in user.items() if k in DEFAULTS})
        for key, (was_default, now_default) in MIGRATED_DEFAULTS.items():
            if user.get(key) == was_default and DEFAULTS[key] == now_default:
                cfg[key] = now_default
                log.info("Настройка %s обновлена со старого умолчания %r на %r",
                         key, was_default, now_default)
        # старый путь данных CLI-версии равнозначен "auto": переносом займётся paths.migrate_legacy
        if cfg["data_dir"] == "~/.voice_tool" and os.name == "nt":
            cfg["data_dir"] = "auto"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Создан файл настроек: %s", path)
    conf = Config(cfg)
    conf["_path"] = str(path)
    return conf
