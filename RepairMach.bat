@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo RepairMach Portable Beta
echo.

".\runtime\python\python.exe" ".\app\repairmach_beta.py"

echo.
pause