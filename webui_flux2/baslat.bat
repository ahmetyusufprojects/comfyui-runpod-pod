@echo off
REM WebUI bagimsiz baslar; gorsel/video uretimi icin ComfyUI'yi ayri baslatin.
cd /d "%~dp0"
"..\python_embeded\python.exe" server.py
pause
