@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Builds the app (build.bat), then the installer:
rem      dist\ZuqongsoftMusic-Setup-<version>.exe
rem  Usage:  build_installer.bat              build everything
rem          build_installer.bat /skipbuild   reuse the existing dist\ZuqongsoftMusic
rem  Needs Inno Setup 6.3+:  winget install JRSoftware.InnoSetup
rem ---------------------------------------------------------------------------

set "APPNAME=ZuqongsoftMusic"
pushd "%~dp0" || (echo Could not open the project folder. & goto :fail)
set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%ROOT%\installer.iss" (echo ERROR: installer.iss is missing. & goto :fail)

rem ---- 1. Build the app ----------------------------------------------------------
if /i "%~1"=="/skipbuild" goto :built
call "%ROOT%\build.bat" /nopause
if errorlevel 1 goto :fail
:built
if not exist "%ROOT%\dist\%APPNAME%\%APPNAME%.exe" goto :no_build

rem ---- 2. Find Inno Setup's compiler -------------------------------------------------
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do (
    if exist "%%~P" set "ISCC=%%~P"
)
if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do set "ISCC=%%I"
if not defined ISCC goto :no_inno

rem ---- 3. Version (from version_info.txt) ------------------------------------------------
set "VERSION="
if exist "%PY%" for /f "delims=" %%V in ('call "%PY%" "%ROOT%\tools\app_version.py"') do set "VERSION=%%V"
if not defined VERSION set "VERSION=1.0.0"

rem ---- 4. Compile the installer -----------------------------------------------------------
echo.
echo Building the installer for version %VERSION%...
"%ISCC%" /Qp "/DMyAppVersion=%VERSION%" "%ROOT%\installer.iss"
if errorlevel 1 goto :fail
set "SETUP=%ROOT%\dist\%APPNAME%-Setup-%VERSION%.exe"
if not exist "%SETUP%" goto :fail

echo.
echo Installer ready:
echo   "%SETUP%"
echo This single file is all you need to share.
set "RC=0"
goto :end

:no_build
echo.
echo ERROR: "dist\%APPNAME%\%APPNAME%.exe" was not found. Run build.bat first, or run this
echo script without /skipbuild.
goto :fail

:no_inno
echo.
echo ERROR: Inno Setup was not found. Install it (free) with:
echo   winget install JRSoftware.InnoSetup
echo or from https://jrsoftware.org/isdl.php, then run this script again.
goto :fail

:fail
echo.
echo INSTALLER BUILD FAILED - scroll up to see the first error.
set "RC=1"

:end
popd
if /i not "%~1"=="/nopause" if /i not "%~2"=="/nopause" pause
exit /b %RC%
