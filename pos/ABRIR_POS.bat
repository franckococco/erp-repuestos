@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0..;%PYTHONPATH%

echo.
echo === HAFID POS (presupuestos) ===
echo URL: http://127.0.0.1:8766
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Primera apertura: preparando el POS...
  python -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, firebase_admin; from google.cloud import firestore" >nul 2>&1
if errorlevel 1 (
  echo Reparando dependencias del POS...
  ".venv\Scripts\python.exe" -m pip install --upgrade --force-reinstall -r requirements.txt
  if errorlevel 1 goto :error
)

if exist "%~dp0..\firebase_claves.json" (
  echo Firebase: ENCONTRADO en erp-repuestos\firebase_claves.json
) else if exist "%~dp0firebase_claves.json" (
  echo Firebase: ENCONTRADO en pos\firebase_claves.json
) else if defined FIREBASE_CREDENTIALS_PATH (
  echo Firebase: usando FIREBASE_CREDENTIALS_PATH=%FIREBASE_CREDENTIALS_PATH%
) else (
  echo Firebase: NO hay claves en esta PC
  echo   Copiá firebase_claves.json a:
  echo   %~dp0..\firebase_claves.json
  echo   Despues abri el POS y toca "Conectar Firebase".
)
echo Factura ARCA: despues.
echo Cerra esta ventana para frenar el servidor.
echo.

".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8766 --reload
pause
exit /b 0

:error
echo.
echo No se pudo preparar el POS. Saca una foto de este error.
pause
exit /b 1
