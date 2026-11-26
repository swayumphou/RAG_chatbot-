@echo off
rem Double-click to start the Telegram bot. Ollama must be running.
rem Run with --check to test the index and the models without contacting Telegram.
setlocal
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "BOT=%~dp0src\telegram_bot.py"
if not exist "%PYTHON%" (
    echo The project's Python environment is missing. See README.md for setup.
    pause
    exit /b 1
)
"%PYTHON%" "%BOT%" %*
set "BOT_EXIT=%ERRORLEVEL%"
if not "%BOT_EXIT%"=="0" pause
exit /b %BOT_EXIT%
