@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "SectionStudioScript=%~f1"
if not defined SectionStudioScript set /p "SectionStudioScript=Path to downloaded .vspscript file: "
set "SectionStudioScript=%SectionStudioScript:"=%"
if not exist "%SectionStudioScript%" (
  echo Script file was not found.
  pause
  exit /b 1
)
for %%F in ("%SectionStudioScript%") do set "SectionStudioScript=%%~fF"
set "SectionStudioExe=%SECTION_STUDIO_VSP_EXE%"
if not defined SectionStudioExe set /p "SectionStudioExe=Path to OpenVSP vspscript.exe or vsp.exe: "
set "SectionStudioExe=%SectionStudioExe:"=%"
if not exist "%SectionStudioExe%" (
  echo OpenVSP executable was not found.
  pause
  exit /b 1
)
for %%F in ("%SectionStudioExe%") do set "SectionStudioExe=%%~fF"
for %%F in ("%SectionStudioScript%") do set "SectionStudioDir=%%~dpF"
set "SectionStudioLog=%TEMP%\SectionStudio_%RANDOM%_%RANDOM%.log"
pushd "%SectionStudioDir%"
if errorlevel 1 (
  echo Cannot open the script directory.
  pause
  exit /b 1
)
"%SectionStudioExe%" -script "%SectionStudioScript%" > "%SectionStudioLog%" 2>&1
type "%SectionStudioLog%"
findstr /C:"SECTION_STUDIO_WARNING:" "%SectionStudioLog%" >nul
if not errorlevel 1 echo GEOMETRY WARNING: inspect the VSP3 before using it.
findstr /C:"SECTION_STUDIO_SAVED:" "%SectionStudioLog%" >nul
if errorlevel 1 (
  echo.
  echo Export did not report success. See the messages above.
  echo Log: "%SectionStudioLog%"
) else (
  echo.
  echo The new VSP3 is in: "%SectionStudioDir%"
  echo Open the file named after SECTION_STUDIO_SAVED in OpenVSP.
)
popd
pause
endlocal
