@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

where py >nul 2>nul
if not errorlevel 1 (
    py -3 price_compare.py
    if not errorlevel 1 goto end
)

where python >nul 2>nul
if not errorlevel 1 (
    python price_compare.py
    if not errorlevel 1 goto end
)

echo.
echo Python 3 was not found or the program could not start.
echo Install Python 3 from https://www.python.org/downloads/
echo During installation, enable "Add Python to PATH".
echo Then double-click this file again.
echo.
pause

:end
endlocal
