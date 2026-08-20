@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo       Building MasdiFox Android APK (.apk)
echo ===================================================

set ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
set ANDROID_SDK_ROOT=%LOCALAPPDATA%\Android\Sdk

if not exist "android\local.properties" (
    echo sdk.dir=%LOCALAPPDATA:\=\\%\\Android\\Sdk> android\local.properties
)

echo [1/3] Syncing Capacitor Assets and Config...
call npx cap sync android
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Capacitor sync failed!
    pause
    exit /b %ERRORLEVEL%
)

echo [2/3] Compiling Android APK with Gradle...
cd android
call gradlew.bat assembleDebug
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Gradle build failed!
    cd ..
    pause
    exit /b %ERRORLEVEL%
)

cd ..
echo.
echo ===================================================
echo [3/3] BUILD BERHASIL!
echo Lokasi APK: android\app\build\outputs\apk\debug\app-debug.apk
echo ===================================================
pause
