@echo off
setlocal EnableExtensions

rem ---------------------------------------------------------------------------
rem  Builds Zuqongsoft Music with Nuitka: your Python code is compiled to native
rem  machine code (faster start-up, much harder to reverse-engineer, fewer
rem  antivirus false alarms than a PyInstaller build).
rem
rem  Output: dist\ZuqongsoftMusic\ZuqongsoftMusic.exe - the SAME folder build.bat
rem  makes, so the installer packages it unchanged:
rem      build_installer.bat /nuitka        build with Nuitka, then the installer
rem      build_installer.bat /skipbuild     package an existing build
rem
rem  Usage:  build_nuitka.bat [/nopause]
rem  The first build takes 10-30 minutes; later builds are faster (cached).
rem ---------------------------------------------------------------------------

set "APPNAME=ZuqongsoftMusic"
pushd "%~dp0" || (echo Could not open the project folder. & goto :fail)
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "DIST=%ROOT%\dist"
set "WORK=%ROOT%\build\nuitka"
set "OUT=%WORK%\%APPNAME%.dist"
set "EXE=%DIST%\%APPNAME%\%APPNAME%.exe"

rem ---- 1. Project files ------------------------------------------------------------
set "MISSING="
for %%F in (main.py requirements.txt version_info.txt app\__init__.py app\ui.py app\engine.py app\libmpv.py app\equalizer.py app\equalizer_ui.py app\audio_devices.py app\theme.py app\theme_picker.py app\lyrics.py app\lyrics_editor.py app\panels.py app\stats_page.py app\play_tracker.py app\responsive.py app\sound_ui.py app\studio.py app\studio_ui.py app\tools.py app\loudness_scan.py app\radio.py app\fullscreen.py app\mini.py app\models.py app\library.py app\system_media.py tools\get_libmpv.py tools\app_version.py assets\icon.ico assets\icon.png) do (
    if not exist "%ROOT%\%%F" call set "MISSING=%%MISSING%% %%F"
)
if defined MISSING goto :missing_files

rem ---- 2. Virtual environment ---------------------------------------------------------
if exist "%PY%" "%PY%" -c "import sys" >nul 2>nul && goto :venv_ready
echo Creating the virtual environment...
if exist "%VENV%" rmdir /s /q "%VENV%"
py -3 -m venv "%VENV%" >nul 2>nul || python -m venv "%VENV%" || goto :no_python
:venv_ready

rem ---- 3. Dependencies + Nuitka ------------------------------------------------------------
echo Installing dependencies and Nuitka...
"%PY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%PY%" -m pip install -r "%ROOT%\requirements.txt" nuitka ordered-set --quiet --disable-pip-version-check || goto :fail

rem ---- 4. Audio engine ---------------------------------------------------------------------
if exist "%ROOT%\lib\libmpv-2.dll" goto :have_mpv
echo Downloading the audio engine (libmpv)...
"%PY%" "%ROOT%\tools\get_libmpv.py" || goto :fail
:have_mpv

rem ---- 5. Version (from version_info.txt) --------------------------------------------------
set "VERSION="
for /f "delims=" %%V in ('call "%PY%" "%ROOT%\tools\app_version.py"') do set "VERSION=%%V"
if not defined VERSION set "VERSION=1.0.0"

rem ---- 6. Compile ----------------------------------------------------------------------
taskkill /im %APPNAME%.exe /f >nul 2>nul
echo.
echo Compiling version %VERSION% with Nuitka. This takes a while the first time...
"%PY%" -m nuitka --mode=standalone ^
  --enable-plugins=pyside6 --include-qt-plugins=sensible,multimedia ^
  --include-package=winrt ^
  --nofollow-import-to=PIL --nofollow-import-to=numpy --nofollow-import-to=tkinter ^
  --include-data-dir="%ROOT%\assets=assets" ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico="%ROOT%\assets\icon.ico" ^
  --company-name="Zuqongtech" --product-name="Zuqongsoft Music" --file-description="Zuqongsoft Music" ^
  --file-version=%VERSION% --product-version=%VERSION% --copyright="Copyright (c) 2026 Zuqongtech" ^
  --output-dir="%WORK%" --output-folder-name=%APPNAME% --output-filename=%APPNAME%.exe ^
  --assume-yes-for-downloads ^
  "%ROOT%\main.py"
if errorlevel 1 goto :compile_failed
if not exist "%OUT%\%APPNAME%.exe" goto :compile_failed

rem ---- 7. Put the result where build.bat would, plus the audio engine -------------------
if exist "%DIST%\%APPNAME%" rmdir /s /q "%DIST%\%APPNAME%"
if exist "%DIST%\%APPNAME%" goto :locked
if not exist "%DIST%" mkdir "%DIST%"
move "%OUT%" "%DIST%\%APPNAME%" >nul || goto :fail
copy /y "%ROOT%\lib\libmpv-2.dll" "%DIST%\%APPNAME%\" >nul || goto :fail
if not exist "%EXE%" goto :fail

echo.
echo Nuitka build succeeded:
echo   "%EXE%"
echo Make the installer with:  build_installer.bat /skipbuild
set "RC=0"
goto :end

:missing_files
echo.
echo ERROR: These project files are missing:
for %%F in (%MISSING%) do echo   %%F
echo Copy them from the latest version of the project, then run build_nuitka.bat again.
goto :fail

:no_python
echo.
echo ERROR: Could not create a virtual environment.
echo Install 64-bit Python 3.10 - 3.13 from python.org and tick "Add python.exe to PATH".
goto :fail

:compile_failed
echo.
echo ERROR: Nuitka could not build the app. Scroll up for the first error. Common causes:
echo  - No C compiler: install "Build Tools for Visual Studio" with the
echo    "Desktop development with C++" workload, then run this script again.
echo  - Antivirus blocking the compiler: exclude "%WORK%" in Windows Security.
echo  - Out of memory: close other programs (Nuitka needs several GB for PySide6).
goto :fail

:locked
echo.
echo ERROR: "%DIST%\%APPNAME%" is in use. Close Zuqongsoft Music and any Explorer window
echo showing that folder, then run this script again.
goto :fail

:fail
echo.
echo NUITKA BUILD FAILED - scroll up to see the first error.
set "RC=1"

:end
popd
if /i not "%~1"=="/nopause" pause
exit /b %RC%