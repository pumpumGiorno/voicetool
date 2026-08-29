"""Поставить CUDA-библиотеки, нужные faster-whisper для работы на видеокарте.

Зачем отдельный скрипт. cuBLAS и cuDNN весят около двух гигабайт — класть их внутрь
VoiceTool.exe неразумно (сборка распухла бы в семь раз ради тех, у кого нет NVIDIA).
Поэтому библиотеки ставятся отдельно, один раз:

  * при запуске из исходников достаточно `pip install -r requirements-gpu.txt`
    (или просто запустить этот скрипт — он сделает то же самое);
  * для собранного exe скрипт кладёт их в %APPDATA%\\VoiceTool\\cuda\\, где VoiceTool
    их и ищет.

    python tools/install_cuda.py            — в venv (для запуска из исходников)
    python tools/install_cuda.py --for-exe  — в папку данных (для VoiceTool.exe)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ровно то, что требует CTranslate2 4.x: cuBLAS из CUDA 12 и cuDNN 9
PACKAGES = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]


def main():
    from voicetool import cuda

    for_exe = "--for-exe" in sys.argv
    print("Voice Tool — установка библиотек для работы на видеокарте\n")

    if cuda.gpu_count() == 0:
        print("Видеокарта с поддержкой CUDA не найдена.")
        print("Программа продолжит работать на процессоре — это нормально и ничего не сломает.")
        return 1

    print(f"Видеокарта: {cuda._gpu_name() or 'NVIDIA (имя не определилось)'}")
    print("Будут скачаны cuBLAS (CUDA 12) и cuDNN 9 — около 2 ГБ. Это разово.\n")

    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if for_exe:
        target = cuda.user_cuda_dir()
        target.mkdir(parents=True, exist_ok=True)
        cmd += ["--target", str(target)]
        print(f"Куда: {target}  (сюда смотрит VoiceTool.exe)")
    else:
        print(f"Куда: текущее окружение Python ({sys.executable})")
    cmd += PACKAGES

    print("\n$ " + " ".join(cmd[:5]) + " ...\n")
    code = subprocess.run(cmd).returncode
    if code:
        print("\nУстановка не удалась. Проверьте интернет и свободное место на диске.")
        return code

    # проверяем результат на месте, а не «должно заработать»
    cuda._state.update(prepared=False, dirs=[], available=None, reason="")
    ok, reason = cuda.probe()
    print()
    if ok:
        print("Готово: видеокарта доступна. Voice Tool будет считать на ней.")
        print("Проверить: python voice_tool.py check  (строка «Бэкенд»)")
        return 0
    print(f"Библиотеки поставлены, но GPU всё ещё недоступен: {reason}")
    print("Обновите драйвер NVIDIA и попробуйте снова. Программа продолжит работать на процессоре.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
