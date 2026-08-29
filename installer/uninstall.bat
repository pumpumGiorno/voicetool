@echo off
rem Удаление Voice Tool. Данные пользователя по умолчанию остаются на месте —
rem чтобы случайное удаление программы не стирало накопленную статистику.

setlocal
chcp 65001 >nul
set "DEST=%LOCALAPPDATA%\Programs\VoiceTool"
set "MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Voice Tool.lnk"

echo.
echo   Voice Tool — удаление
echo   ---------------------
echo   Программа: %DEST%
echo   Данные:    %APPDATA%\VoiceTool  (останутся)
echo.
choice /C YN /N /M "   Удалить программу? [Y/N] "
if errorlevel 2 goto :end

taskkill /IM VoiceTool.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul

rem автозапуск снимаем, иначе Windows будет искать удалённый файл
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v VoiceTool /f >nul 2>&1

if exist "%DEST%" rmdir /s /q "%DEST%"
if exist "%MENU%" del /q "%MENU%"
powershell -NoProfile -Command ^
  "$p=[Environment]::GetFolderPath('Desktop')+'\Voice Tool.lnk'; if (Test-Path $p) { Remove-Item $p }"

echo.
echo   Программа удалена.
echo   Статистика и история остались в %APPDATA%\VoiceTool
echo   Удалить их можно вручную, если они больше не нужны.
echo.

:end
endlocal
pause
