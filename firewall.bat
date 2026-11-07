@echo off
rem Adds Windows Firewall rules so other PCs on your network can reach Zap Drop.
rem Ports: 8765 (app), 8766 (encrypted), 47821 UDP (discovery). Private/Domain networks only.

net session >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

netsh advfirewall firewall delete rule name="Zap Drop" >nul 2>&1
netsh advfirewall firewall add rule name="Zap Drop" dir=in action=allow protocol=TCP localport=8765-8766 profile=private,domain >nul
netsh advfirewall firewall add rule name="Zap Drop" dir=in action=allow protocol=UDP localport=47821 profile=private,domain >nul
echo.
echo  Firewall rules added for Zap Drop (Private and Domain networks).
echo.
echo  Your network connections:
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table Name, InterfaceAlias, NetworkCategory -AutoSize"
echo  If your home network shows "Public" above, Zap Drop will be blocked.
echo  Change it in Settings ^> Network ^& Internet ^> Ethernet ^> (your network) ^> Private.
echo.
pause
