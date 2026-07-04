@echo off
REM Double-click wrapper for install.ps1 -- Windows 11's default PowerShell
REM execution policy often blocks a .ps1 from running via double-click
REM (it just opens in a text editor, or errors "cannot be loaded because
REM running scripts is disabled"). This runs it with -ExecutionPolicy
REM Bypass for just this one process, which does not change any system or
REM user execution-policy setting.
REM
REM Any arguments passed to this .bat are forwarded to install.ps1, e.g.:
REM   install.bat -Port 9000 -Autostart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
