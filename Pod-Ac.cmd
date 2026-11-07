@echo off
setlocal
chcp 65001 >nul
"%~dp0..\python_embeded\python.exe" "%~dp0pod-launch.py" %*
pause
