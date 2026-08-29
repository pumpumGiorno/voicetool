@echo off
rem Установка Voice Tool для текущего пользователя.
rem Права администратора не нужны: программа кладётся в профиль пользователя.
rem Данные (счётчик, история, настройки) живут отдельно в %APPDATA%\VoiceTool
rem и переустановкой не затрагиваются.

setlocal
chcp 65001 >nul
set "SRC=%~dp0..\dist\VoiceTool"
set "DEST=%LOCALAPPDATA%\Programs\VoiceTool"

echo.
echo   Voice Tool — установка
echo   ----------------------
echo   Откуда: %SRC%
echo   Куда:   %DEST%
echo.

if not exist "%SRC%\VoiceTool.exe" (
    echo   [Ошибка] Не найден %SRC%\VoiceTool.exe
    echo   Сначала соберите программу:  python build_exe.py
    echo.
    pause
    exit /b 1
)

if exist "%DEST%\VoiceTool.exe" (
    echo   Найдена установленная версия — закрываю её...
    taskkill /IM VoiceTool.exe /F >nul 2>&1
    timeout /t 2 /nobreak >nul
)

echo   Копирую файлы...
robocopy "%SRC%" "%DEST%" /MIR /NJH /NJS /NDL /NP /NFL >nul
if errorlevel 8 (
    echo   [Ошибка] Не удалось скопировать файлы.
    pause
    exit /b 1
)

echo   Создаю ярлыки...
set "MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%MENU%\Voice Tool.lnk');" ^
  "$s.TargetPath='%DEST%\VoiceTool.exe'; $s.WorkingDirectory='%DEST%';" ^
  "$s.Description='Голосовой ввод и расшифровка записей'; $s.Save()"
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Voice Tool.lnk');" ^
  "$s.TargetPath='%DEST%\VoiceTool.exe'; $s.WorkingDirectory='%DEST%'; $s.Save()"

echo.
echo   Готово.
echo   Программа:  %DEST%\VoiceTool.exe
echo   Ярлыки:     меню «Пуск» и рабочий стол
echo   Данные:     %APPDATA%\VoiceTool
echo.
echo   Автозапуск с Windows включается в настройках программы.
echo.
choice /C YN /N /M "   Запустить Voice Tool сейчас? [Y/N] "
if errorlevel 2 goto :end
start "" "%DEST%\VoiceTool.exe"

:end
endlocal
