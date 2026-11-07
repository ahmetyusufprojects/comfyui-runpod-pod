@echo off
setlocal
chcp 65001 >nul
if not "%~1"=="" goto arguments
echo.
echo 1. Pod'u aç - mevcut WebUI hesabı
echo 2. İlk kurulum - yeni WebUI hesabı
echo 3. Pod'u kapat - önce WebUI'de kasayı mühürleyin
choice /c 123 /n /m "Seçim: "
if errorlevel 3 goto stop
if errorlevel 2 goto first
"%~dp0..\python_embeded\python.exe" "%~dp0pod-launch.py" --restore
goto done
:first
"%~dp0..\python_embeded\python.exe" "%~dp0pod-launch.py"
goto done
:stop
"%~dp0..\python_embeded\python.exe" "%~dp0pod-launch.py" --stop
goto done
:arguments
"%~dp0..\python_embeded\python.exe" "%~dp0pod-launch.py" %*
:done
pause
