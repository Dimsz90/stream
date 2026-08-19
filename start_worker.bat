@echo off
title Anime Archiver Worker (Mini PC)
cd /d "%~dp0"

echo ===================================================
echo   Starting Anime Archiver Worker on Windows Mini PC
echo ===================================================

:: Periksa Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python tidak ditemukan! Harap install Python 3.10+ dan centang 'Add Python to PATH'.
    pause
    exit /b
)

:: Buat venv jika belum ada
if not exist "venv" (
    echo [1/3] Membuat Python virtual environment venv...
    python -m venv venv
)

:: Aktifkan venv dan install requirements
echo [2/3] Mengaktifkan venv dan memeriksa / menginstall dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

:: Jalankan Worker
echo [3/3] Menjalankan worker.py...
echo ===================================================
python worker.py 15

pause
