@echo off
setlocal

set ADB="%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
set APK="android\app\build\outputs\apk\debug\app-debug.apk"

echo ===================================================
echo     MasdiFox Wireless ADB Installer
echo ===================================================
echo.

if not exist %APK% (
    echo [ERROR] File APK belum ditemukan. Silakan jalankan build-apk.bat terlebih dahulu!
    pause
    exit /b 1
)

set /p IP_PORT="Masukkan IP dan Port HP (contoh: 192.168.1.10:5555 atau 192.168.1.10:xxxxx): "

if "%IP_PORT%"=="" (
    echo [ERROR] Alamat IP dan Port tidak boleh kosong!
    pause
    exit /b 1
)

echo.
echo [1/2] Menghubungkan ke %IP_PORT%...
%ADB% connect %IP_PORT%

echo.
echo [2/2] Menginstal APK ke perangkat...
%ADB% install -r %APK%

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ===================================================
    echo [SUCCESS] MasdiFox berhasil diinstal ke HP Anda!
    echo ===================================================
) else (
    echo.
    echo [ERROR] Gagal menginstal. Pastikan Wireless Debugging aktif dan IP/Port sudah sesuai.
)

pause
