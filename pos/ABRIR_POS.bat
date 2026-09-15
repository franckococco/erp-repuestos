@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0..;%PYTHONPATH%

echo.
echo === HAFID POS (presupuestos) ===
echo URL: http://127.0.0.1:8766
echo.

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

python -m uvicorn server:app --host 127.0.0.1 --port 8766 --reload
pause
