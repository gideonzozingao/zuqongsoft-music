@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Builds dist\%APPNAME%\%APPNAME%.exe
rem  Usage:  build.bat            (pauses at the end so you can read the output)
rem          build.bat /nopause   (for terminals / CI)
rem ---------------------------------------------------------------------------

rem Must match NAME in MusicPlayer.spec.
set "APPNAME=ZuqongsoftMusic"

set "NOPAUSE="
for %%A in (%*) do (
    if /i "%%~A"=="/nopause" set "NOPAUSE=1"
)

rem pushd (not cd) also works when the project is on a network share or \\wsl$ path.
pushd "%~dp0" || (echo Could not open the project folder. & goto :fail)
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "SPEC=%ROOT%\MusicPlayer.spec"
set "DIST=%ROOT%\dist"
set "WORK=%ROOT%\build"
set "EXE=%DIST%\%APPNAME%\%APPNAME%.exe"

echo Project folder: "%ROOT%"
if not exist "%SPEC%" goto :no_spec

rem Every file the build needs. Missing ones are listed instead of building a broken app.
set "MISSING="
for %%F in (main.py requirements.txt version_info.txt app\ui.py app\engine.py app\libmpv.py app\equalizer.py app\equalizer_ui.py app\audio_devices.py app\theme_picker.py app\lyrics.py app\lyrics_editor.py app\panels.py app\stats_page.py app\play_tracker.py app\responsive.py app\sound_ui.py app\studio.py app\studio_ui.py app\tools.py app\loudness_scan.py app\radio.py app\fullscreen.py app\mini.py tools\get_libmpv.py assets\icon.ico assets\icon.png) do (
    if not exist "%ROOT%\%%F" call set "MISSING=%%MISSING%% %%F"
)
if defined MISSING goto :missing_files

rem ---- 1. Virtual environment (recreated if missing or broken) ----------------
if exist "%PY%" goto :venv_ready
echo.
echo Creating virtual environment...
if exist "%VENV%" rmdir /s /q "%VENV%"
where py >nul 2>nul && py -3 -m venv "%VENV%"
if not exist "%PY%" python -m venv "%VENV%"
if not exist "%PY%" goto :no_python
:venv_ready
"%PY%" --version

rem ---- 2. Dependencies -------------------------------------------------------
rem Call pip and PyInstaller through the venv's python.exe so a global
rem PyInstaller on PATH can never be used by mistake.
echo.
echo Installing dependencies...
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r "%ROOT%\requirements.txt" "pyinstaller>=6.10" || goto :fail

rem ---- 3. libmpv (audio engine with equalizer); optional, the app falls back without it --
if exist "%ROOT%\lib\libmpv-2.dll" goto :have_mpv
echo.
echo Downloading libmpv (audio engine for the equalizer)...
"%PY%" "%ROOT%\tools\get_libmpv.py"
if not exist "%ROOT%\lib\libmpv-2.dll" echo WARNING: libmpv not downloaded. The app will build, but without the equalizer.
:have_mpv

rem ---- 4. Build --------------------------------------------------------------
rem A running copy of the app locks its dist folder and makes the build fail.
taskkill /im %APPNAME%.exe /f >nul 2>nul

echo.
echo Building...
"%PY%" -m PyInstaller --noconfirm --clean --distpath "%DIST%" --workpath "%WORK%" "%SPEC%" || goto :fail

rem ---- 5. Verify -------------------------------------------------------------
if not exist "%EXE%" goto :missing_exe

echo.
echo Build succeeded:
echo   "%EXE%"
echo Share the whole "%DIST%\%APPNAME%" folder as a zip, not only the .exe.
set "RC=0"
goto :end

:no_spec
echo.
echo ERROR: MusicPlayer.spec was not found in "%ROOT%".
echo Put build.bat in the same folder as MusicPlayer.spec and main.py.
goto :fail

:missing_files
echo.
echo ERROR: These project files are missing:
for %%F in (%MISSING%) do echo   %%F
echo Copy them from the latest version of the project into "%ROOT%", then run build.bat again.
goto :fail

:no_python
echo.
echo ERROR: Could not create a virtual environment.
echo Install 64-bit Python 3.10 - 3.13 from python.org and tick "Add python.exe to PATH".
goto :fail

:missing_exe
echo.
echo ERROR: PyInstaller finished but this file does not exist:
echo   "%EXE%"
echo Check that APPNAME at the top of build.bat matches NAME in MusicPlayer.spec.
echo If it does, antivirus probably quarantined the file: open Windows Security,
echo Virus and threat protection, Protection history, restore it, and add
echo "%DIST%" as an exclusion. Then run this script again.
goto :fail

:fail
echo.
echo BUILD FAILED - scroll up to see the first error.
set "RC=1"

:end
popd
if not defined NOPAUSE pause
exit /b %RC%