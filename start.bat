@echo off
set PYTHON_ENV=D:\python_envs\flowkit\python.exe

if not exist "%PYTHON_ENV%" (
    echo [ERROR] Python environment not found at %PYTHON_ENV%
    pause
    exit /b 1
)

"%PYTHON_ENV%" "%~dp0scripts\launcher.py" %*

