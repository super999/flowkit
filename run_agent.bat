@echo off
if not defined FLOW_PROJECT_ID set "FLOW_PROJECT_ID=2bc7c7bc-eb18-4975-9deb-6f93c3df3afb"
title Flow Kit Agent
cd /d "%~dp0"

echo [1/3] Configuring FFmpeg path...
set "PATH=C:\Users\xiawe\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin;%PATH%"

echo [2/3] Activating Conda environment (d:\python_envs\flowkit)...
call "D:\ProgramData\miniconda3\condabin\conda.bat" activate "d:\python_envs\flowkit"

echo [3/3] Starting Flow Kit Agent...
python -m agent.main

pause
