# Windows runtime validation

Этот чек-лист выполняется на чистой Windows 10/11 x64 после сборки
`python build_exe.py`. Linux CI проверяет Python-логику и mocks, но не подтверждает
работу Win32, UI Automation, микрофона, DPI или packaged EXE.

## Матрица окружения

- Windows 10 и Windows 11, обычный пользователь без elevation.
- DPI 100%, 150% и 200%; один монитор и два монитора с разными scale/origin.
- Микрофон default и выбранный вручную; CPU-only и, если доступно, NVIDIA CUDA.
- Ollama выключен, Ollama без выбранной модели и Ollama с `qwen3.5:9b`.
- Steam выключен и запущен; Dota 2 в основной и дополнительной library.

## Обязательные проверки

1. Чистая установка: папка `dist\VoiceTool` запускается, все runtime DLL находятся в
   `_internal`, данные создаются только в `%APPDATA%\VoiceTool`.
2. Wake/STT: «Алиса» распознаётся, обычная диктовка попадает в активное поле Unicode без
   изменения clipboard; opt-out не создаёт новые transcript/history/activity-записи.
3. Fast path с выключенным Ollama: открыть/закрыть/focus/minimize Chrome и Telegram,
   volume 0/30/100, mute/unmute, открыть Downloads и безопасный `txt`/`pdf`.
4. File boundary: `exe`, `bat`, `cmd`, `ps1`, `py`, `lnk` и `url` отклоняются через
   `open_file`; `show_in_folder` только показывает их.
5. Steam: «Открой Dota» находит manifest и запускает `steam://run/570`, включая случай,
   когда Steam сначала выключен; отсутствующая игра возвращает structured error.
6. Multi-step/context: Chrome → Telegram → вернуться в Chrome; Блокнот → русский текст;
   Dota → «сверни её»; failed step корректно передаётся следующему model step.
7. Confirmation/cancel: high-impact действие ждёт подтверждения именно перед tool,
   Cancel ничего не выполняет, Confirm продолжает этот tool, Esc прекращает дальнейшие
   шаги и ожидания.
8. UIA/vision priority: native action не делает screenshot; UIA используется после
   native failure; vision — только последний local fallback. Проверить max vision steps,
   повторный screenshot/UI tree/foreground verification и отказ при ошибке проверки.
9. Privacy/injection: screenshot не сохраняется и не попадает в activity/diagnostic log;
   экранный текст `ignore previous instructions`, `delete files`, `send password` остаётся
   untrusted observation и не меняет confirmation policy.
10. Coordinates: клики валидируются на каждом мониторе при всех DPI; outside virtual
    desktop и stale coordinates отклоняются до действия.
11. Performance: в idle overlay не вызывает постоянные repaint; Reduced motion отключает
    pulse/grow/card/fade. Проверить CPU в idle после пяти минут.
12. Shutdown: во время listen, model request, multi-step confirmation и file transcription
    выйти через tray. Процесс завершается после cancellation timeout, микрофон освобождён,
    фоновых VoiceTool/Python процессов не остаётся.
13. Backward compatibility: запустить с config от предыдущего релиза, включая legacy
    `reduce_motion`; неизвестные ключи не ломают старт, новые defaults добавляются.
14. Packaging: проверить обычную папочную сборку и, если публикуется, `--onefile`;
    `python tools/test_exe.py` проходит на собранном артефакте. Исходный checkout не
    содержит сгенерированные `voicetool\VoiceTool.exe`/`voicetool\_internal`.
