@echo off
if not defined FLOW_PROJECT_ID set "FLOW_PROJECT_ID=2bc7c7bc-eb18-4975-9deb-6f93c3df3afb"
set PYTHON_ENV=D:\python_envs\flowkit\python.exe

if not exist "%PYTHON_ENV%" (
    echo [ERROR] Python environment not found at %PYTHON_ENV%
    pause
    exit /b 1
)

"%PYTHON_ENV%" "%~dp0scripts\launcher.py" %*
