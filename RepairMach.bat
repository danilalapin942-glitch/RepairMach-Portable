@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo RepairMach 9.0
echo.

".\runtime\python\python.exe" ".\app\repairmach_beta.py"

echo.
pause
