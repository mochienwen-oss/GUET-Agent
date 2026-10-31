@echo off
rem SAI desktop launcher.
rem
rem Why this copies instead of running in place:
rem   Electron 33 exits immediately (code 3, no log at all) when its own path
rem   contains non-ASCII characters, and this project lives under a folder whose
rem   name is Chinese. So both the runtime and a copy of the app files are kept in
rem   %LOCALAPPDATA%\SAI, which is plain ASCII. App files are re-copied on every
rem   launch, so desktop\main.js here stays the single source of truth.
rem
rem Also clears ELECTRON_RUN_AS_NODE / NODE_OPTIONS: with either of them set,
rem Electron runs as plain Node and quietly exits without a window.
setlocal
set "ELECTRON_RUN_AS_NODE="
set "NODE_OPTIONS="
set "HOME_DIR=%LOCALAPPDATA%\SAI"

if not exist "%~dp0node_modules\electron\dist\electron.exe" (
  echo [sai] Electron is not installed yet, running npm install in "%~dp0" ...
  call npm install --prefix "%~dp0"
  if errorlevel 1 goto fail
)

if not exist "%HOME_DIR%\runtime\electron.exe" (
  echo [sai] first run: copying Electron runtime to "%HOME_DIR%\runtime" - about 270 MB ...
  robocopy "%~dp0node_modules\electron\dist" "%HOME_DIR%\runtime" /E /NFL /NDL /NJH /NJS /NP >nul
  if errorlevel 8 goto fail
)

rem Copy every .js (main / preload / campus-*) plus config: a missing file crashes on start
rem server.txt 是可选的（存在时覆盖后端地址），缺失不会让 robocopy 失败
robocopy "%~dp0." "%HOME_DIR%\app" *.js config.json package.json server.txt /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail

rem Copy the campus modules used by main.js and the controller.
robocopy "%~dp0campus" "%HOME_DIR%\app\campus" *.js /E /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto fail

start "" "%HOME_DIR%\runtime\electron.exe" "%HOME_DIR%\app"
exit /b 0

:fail
echo.
echo [sai] failed to prepare the desktop runtime, see the messages above.
echo       common cause: no network for npm install, or %LOCALAPPDATA% is not writable.
pause
exit /b 1
