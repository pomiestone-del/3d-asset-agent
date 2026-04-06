@echo off
cd /d "%~dp0"

:: First-run check: if .setup_done doesn't exist, run environment setup
if not exist ".setup_done" (
    echo [First Run] Checking environment...
    python setup_env.py --auto
    if errorlevel 1 (
        echo.
        echo Environment setup failed. Please fix the issues above.
        pause
        exit /b 1
    )
    echo. > .setup_done
    echo.
)

:: Streamlit opens browser automatically (--server.headless=false is default)
streamlit run app.py
