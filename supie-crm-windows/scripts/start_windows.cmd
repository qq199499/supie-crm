@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "APP_ENTRY=%PROJECT_ROOT%\ops\service_runner.py"

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found: %PYTHON_EXE%
    exit /b 1
)

if not exist "%APP_ENTRY%" (
    echo Application entry not found: %APP_ENTRY%
    exit /b 1
)

pushd "%PROJECT_ROOT%"
"%PYTHON_EXE%" "%APP_ENTRY%"
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
