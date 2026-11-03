@echo off
rem Start the OpenCode voice daemon without a console window (for the Startup folder).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m voice_asr.daemon
