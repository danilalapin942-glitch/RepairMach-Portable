@echo off
setlocal
set OMP_NUM_THREADS=4
set "PATH=%~dp0;%PATH%"
cd /d "%~dp0"
machline.exe
pause
