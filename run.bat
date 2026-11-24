@echo off
setlocal
chcp 65001 >nul
set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0app.py" %*
if errorlevel 1 pause
