"""Подключение CUDA-библиотек и выбор бэкенда: видеокарта или процессор.

Зачем отдельный модуль. CTranslate2 (движок faster-whisper) для работы на GPU требует
cuBLAS из CUDA 12 и cuDNN 9. Сама видеокарта и драйвер тут ни при чём: драйвер может
рапортовать «CUDA 13.2», а нужных DLL в системе не быть — тогда загрузка модели падает
с «Library cublas64_12.dll is not found or cannot be loaded».

На Windows эти DLL не ищутся в PATH процесса Python: с версии 3.8 нужно явно объявить
папку через os.add_dll_directory(). Здесь мы это и делаем — до того, как CTranslate2
попробует открыть CUDA.

Где ищем, по порядку:
  1. %APPDATA%\\VoiceTool\\cuda\\  — куда их кладёт tools/install_cuda.py (вариант для exe:
     класть 2 ГБ библиотек внутрь сборки неразумно);
  2. site-packages/nvidia/*/bin   — пакеты nvidia-cublas-cu12 и nvidia-cudnn-cu12
     (так работает запуск из исходников);
  3. CUDA_PATH / CUDA_HOME       — установленный CUDA Toolkit;
  4. системный PATH              — если библиотеки уже лежат где-то ещё.

Если ничего не нашли или GPU не заработал — молча уходим на процессор и пишем причину
в лог и на страницу проверки. Программа на машине без видеокарты работать не перестаёт.
"""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# без этих двух GPU не поедет; остальные DLL подтягиваются рядом
REQUIRED = ("cublas64_12.dll", "cudnn64_9.dll")

_state = {
    "prepared": False,
    "dirs": [],        # какие папки добавили в поиск DLL
    "available": None, # True / False — годится ли GPU, None — ещё не проверяли
    "reason": "",      # почему нет GPU, человеческим языком
    "device": None,    # что выбрали в итоге: "cuda" | "cpu"
}


def user_cuda_dir() -> Path:
    """Куда tools/install_cuda.py кладёт библиотеки для собранного exe."""
    from . import paths

    return paths.default_data_dir() / "cuda"


def _candidate_dirs():
    """Все места, где могут лежать нужные DLL, в порядке предпочтения."""
    dirs = []

    user_dir = user_cuda_dir()
    if user_dir.is_dir():
        dirs.append(user_dir)
        dirs += [p for p in sorted(user_dir.rglob("bin")) if p.is_dir()]

    # пакеты nvidia-*-cu12 рядом с интерпретатором (запуск из исходников)
    for site in {Path(p) for p in sys.path if p and Path(p).name == "site-packages"} | \
                {Path(sys.prefix) / "Lib" / "site-packages"}:
        nvidia = site / "nvidia"
        if nvidia.is_dir():
            dirs += [p for p in sorted(nvidia.glob("*/bin")) if p.is_dir()]

    # обычный CUDA Toolkit
    for var in ("CUDA_PATH", "CUDA_HOME"):
        root = os.environ.get(var)
        if root and (Path(root) / "bin").is_dir():
            dirs.append(Path(root) / "bin")

    # рядом с exe — на случай, если кто-то положил библиотеки в папку программы
    from . import paths

    bundled = paths.app_dir() / "cuda"
    if bundled.is_dir():
        dirs.append(bundled)

    seen, unique = set(), []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def prepare() -> bool:
    """Объявить папки с CUDA-DLL. True — нужные библиотеки найдены. Идемпотентно."""
    if _state["prepared"]:
        return bool(_state["dirs"])

    _state["prepared"] = True
    if os.name != "nt":
        _state["dirs"] = ["(не Windows: библиотеки ищет сам загрузчик)"]
        return True

    found = {}
    for directory in _candidate_dirs():
        try:
            names = {p.name.lower() for p in directory.glob("*.dll")}
        except OSError:
            continue
        useful = [dll for dll in REQUIRED if dll.lower() in names]
        if not useful:
            continue
        try:
            os.add_dll_directory(str(directory))
        except OSError as e:
            log.debug("Папку %s подключить не вышло: %s", directory, e)
            continue
        # add_dll_directory действует только на «безопасный» поиск библиотек.
        # CTranslate2 грузит cuBLAS обычным LoadLibrary, а тот смотрит в PATH —
        # поэтому папку добавляем и туда. Из исходников это было незаметно:
        # ctranslate2 сам находит пакеты nvidia в site-packages, а в exe их нет.
        current = os.environ.get("PATH", "")
        if str(directory).lower() not in current.lower():
            os.environ["PATH"] = str(directory) + os.pathsep + current
        _state["dirs"].append(str(directory))
        for dll in useful:
            found.setdefault(dll, str(directory))

    missing = [dll for dll in REQUIRED if dll not in found]
    if missing:
        _state["reason"] = ("не найдены библиотеки " + ", ".join(missing)
                            + " (нужны cuBLAS для CUDA 12 и cuDNN 9)")
        log.info("CUDA: %s", _state["reason"])
        return False

    log.info("CUDA: библиотеки найдены в %s", "; ".join(_state["dirs"]))
    return True


def gpu_count() -> int:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception as e:  # ctranslate2 может и не собраться с CUDA
        log.debug("Счётчик CUDA-устройств недоступен: %s", e)
        return 0


def probe():
    """Реально ли работает GPU. (bool, причина). Тяжёлая проверка делается один раз."""
    if _state["available"] is not None:
        return _state["available"], _state["reason"]

    if gpu_count() == 0:
        _state["available"] = False
        _state["reason"] = _state["reason"] or "видеокарта с CUDA не найдена"
        return False, _state["reason"]

    if not prepare():
        _state["available"] = False
        return False, _state["reason"]

    # Наличие DLL ещё не значит, что они загрузятся: бывает несовпадение версий.
    # Дешёвый способ убедиться — попросить CTranslate2 создать что-нибудь на GPU.
    try:
        import ctranslate2

        ctranslate2.get_supported_compute_types("cuda")
        _state["available"] = True
        _state["reason"] = ""
        log.info("CUDA: GPU доступен")
    except Exception as e:
        _state["available"] = False
        _state["reason"] = f"CUDA не инициализируется: {e}"
        log.warning("CUDA: %s", _state["reason"])
    return _state["available"], _state["reason"]


def resolve(device_setting: str, compute_setting: str):
    """Настройки -> (device, compute_type, причина отката).

    device_setting: auto | cuda | cpu.  compute_setting: auto | конкретный тип.
    "auto" на видеокарте берёт float16 (быстро и точно), на процессоре — int8.
    """
    wanted = (device_setting or "auto").lower()
    if wanted == "cpu":
        return "cpu", _compute("cpu", compute_setting), ""

    ok, reason = probe()
    if ok:
        return "cuda", _compute("cuda", compute_setting), ""
    if wanted == "cuda":
        # пользователь просил именно GPU — скажем, почему не вышло, но не упадём
        log.warning("Запрошен GPU, но он недоступен (%s). Работаю на процессоре.", reason)
    return "cpu", _compute("cpu", compute_setting), reason


def _compute(device: str, setting: str) -> str:
    setting = (setting or "auto").lower()
    if setting != "auto":
        return setting
    return "float16" if device == "cuda" else "int8"


def status():
    """Что показать на странице проверки системы."""
    ok, reason = probe()
    return {
        "available": ok,
        "reason": reason,
        "devices": gpu_count(),
        "dirs": list(_state["dirs"]),
        "name": _gpu_name(),
    }


def _gpu_name() -> str:
    """Имя видеокарты через nvidia-smi — исключительно для показа пользователю."""
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=8, check=False)
        return out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError, IndexError):
        return ""


def active_device() -> str:
    """Какой бэкенд выбран сейчас — для интерфейса и логов."""
    return _state["device"] or "?"


def remember(device: str):
    _state["device"] = device
