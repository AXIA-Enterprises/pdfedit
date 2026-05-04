@echo off
REM Build the standalone PDFEdit.exe bundle on Windows.
setlocal
cd /d "%~dp0"

set VENV_DIR=%cd%\.venv
set PY=%VENV_DIR%\Scripts\python.exe

if not exist "%PY%" (
    echo Setting up build venv at %VENV_DIR% ...
    python -m venv "%VENV_DIR%" || goto :err
    "%PY%" -m pip install --quiet --upgrade pip
    "%PY%" -m pip install --quiet -r requirements.txt pyinstaller pillow
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist PDFEdit.spec del /q PDFEdit.spec

if not exist PDFEdit.ico "%PY%" make_icon.py
"%PY%" -m PyInstaller --windowed --noconfirm --name PDFEdit --icon PDFEdit.ico pdfedit.py || goto :err

if exist PDFEdit rmdir /s /q PDFEdit
move /y dist\PDFEdit PDFEdit >nul

rmdir /s /q dist
rmdir /s /q build
del /q PDFEdit.spec

if exist PDFEdit-windows.zip del /q PDFEdit-windows.zip
powershell -NoProfile -Command "Compress-Archive -Path PDFEdit -DestinationPath PDFEdit-windows.zip -Force"

echo Built: PDFEdit\  +  PDFEdit-windows.zip
goto :eof

:err
echo Build failed.
exit /b 1
