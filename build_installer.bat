@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Builds the app (build.bat), then the installer:
rem      dist\ZuqongsoftMusic-Setup-<version>.exe
rem  Usage:  build_installer.bat              build with PyInstaller, then the installer
rem          build_installer.bat /nuitka      build with Nuitka (compiled), then the installer
rem          build_installer.bat /skipbuild   reuse the existing dist\ZuqongsoftMusic
rem          add /nopause to any of these to skip the final pause
rem  Needs Inno Setup 6.3+:  winget install JRSoftware.InnoSetup
rem ---------------------------------------------------------------------------

set "APPNAME=ZuqongsoftMusic"
set "SKIPBUILD="
set "NOPAUSE="
set "BUILDER=build.bat"
for %%A in (%*) do (
    if /i "%%~A"=="/skipbuild" set "SKIPBUILD=1"
    if /i "%%~A"=="/nopause" set "NOPAUSE=1"
    if /i "%%~A"=="/nuitka" set "BUILDER=build_nuitka.bat"
)
pushd "%~dp0" || (echo Could not open the project folder. & goto :fail)
set "ROOT=%CD%"
set "PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%ROOT%\installer.iss" (echo ERROR: installer.iss is missing. & goto :fail)

rem ---- 1. Build the app ----------------------------------------------------------
if defined SKIPBUILD goto :built
if not exist "%ROOT%\%BUILDER%" (echo ERROR: %BUILDER% is missing. & goto :fail)
call "%ROOT%\%BUILDER%" /nopause
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

rem ---- 4. Compile the installer (signed when signing is configured) ------------------------
set "SIGNCMD=powershell.exe -NoProfile -ExecutionPolicy Bypass -File $q%ROOT%\tools\sign.ps1$q $f"
set "WANT_SIGN="
if exist "%ROOT%\signing.env" set "WANT_SIGN=1"
if defined SIGN_CERT_SHA1 set "WANT_SIGN=1"
if defined SIGN_PFX set "WANT_SIGN=1"
if defined SIGN_DLIB set "WANT_SIGN=1"
if not defined WANT_SIGN goto :compile_unsigned
echo.
echo Checking code signing...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\tools\sign.ps1" -Check
if errorlevel 1 goto :sign_not_ready
echo.
echo Building the SIGNED installer for version %VERSION%...
"%ISCC%" /Qp "/DMyAppVersion=%VERSION%" /DUseSignTool "/Szsign=%SIGNCMD%" "%ROOT%\installer.iss"
if errorlevel 1 goto :fail
goto :compiled
:compile_unsigned
echo.
echo Building the installer for version %VERSION% (unsigned: signing isn't configured)...
"%ISCC%" /Qp "/DMyAppVersion=%VERSION%" "%ROOT%\installer.iss"
if errorlevel 1 goto :fail
:compiled
set "SETUP=%ROOT%\dist\%APPNAME%-Setup-%VERSION%.exe"
if not exist "%SETUP%" goto :fail

rem ---- 5. SHA-256 checksum for the release notes ---------------------------------------------
set "HASH="
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath '%SETUP%').Hash.ToLower()"`) do set "HASH=%%H"
if defined HASH > "%SETUP%.sha256" echo %HASH% *%APPNAME%-Setup-%VERSION%.exe

echo.
echo Installer ready:
echo   "%SETUP%"
if defined HASH echo SHA-256: %HASH%   (also saved next to it as .sha256)
echo This single file is all you need to share.
set "RC=0"
goto :end

:sign_not_ready
echo.
echo Signing is switched on by signing.env, but it isn't ready - see the message above.
echo Fix it, or delete signing.env to build an unsigned installer.
goto :fail

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
if not defined NOPAUSE pause
exit /b %RC%