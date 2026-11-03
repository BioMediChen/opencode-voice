@echo off
rem Start the OpenCode voice daemon in this console.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m voice_asr.daemon %*
