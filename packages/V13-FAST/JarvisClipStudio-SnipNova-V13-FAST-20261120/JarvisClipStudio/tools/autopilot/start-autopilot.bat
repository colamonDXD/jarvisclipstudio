@echo off
rem Jarvis Autopilot launcher.
rem Watches D:\Jarvis_Autopilot\INBOX and feeds videos to JarvisClipStudio.
rem Make sure the app itself is running first (docker compose up -d),
rem and that settings.json beside autopilot.py contains your API key.
cd /d %~dp0
python autopilot.py "D:\Jarvis_Autopilot"
pause
