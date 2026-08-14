@echo off
title Flow Kit Agent
cd /d "%~dp0"

echo [1/3] Configuring FFmpeg path...
set "PATH=C:\Users\xiawe\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin;%PATH%"

echo [2/3] Activating Conda environment (d:\python_envs\flowkit)...
call "D:\ProgramData\miniconda3\condabin\conda.bat" activate "d:\python_envs\flowkit"

echo [3/3] Starting Flow Kit Agent...
python -m agent.main

pause
