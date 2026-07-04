@echo off
REM Double-click wrapper for uninstall.ps1 -- see install.bat's comment for why.
REM Any arguments passed to this .bat are forwarded to uninstall.ps1, e.g.:
REM   uninstall.bat -RemoveData
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
