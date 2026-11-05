@echo off
REM Sag tik -> "Yonetici olarak calistir" ile calistir. Sadece bir kez gerekli.
netsh advfirewall firewall add rule name="Flux2 WebUI 8189" dir=in action=allow protocol=TCP localport=8189 profile=private
echo.
echo Kural eklendi. Bu pencereyi kapatip baslat.bat'i normal calistirabilirsin.
pause
