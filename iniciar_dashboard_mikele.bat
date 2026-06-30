@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Mikele Dashboard Local
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual local...
    python -m venv .venv
)

if not exist ".venv\Lib\site-packages\fastapi" (
    echo Instalando dependencias por primera vez...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo No se pudieron instalar las dependencias.
        echo Revisa tu conexion a internet y vuelve a intentar.
        pause
        exit /b 1
    )
)

echo.
echo Verificando servidor local...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod 'http://127.0.0.1:8080/api/health' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo Iniciando servidor local...
    start "Mikele Dashboard Server" cmd /k ""%~dp0.venv\Scripts\python.exe" "%~dp0app.py""
)

echo Esperando a que el servidor este listo...
for /l %%i in (1,1,30) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-RestMethod 'http://127.0.0.1:8080/api/health' -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }"
    if not errorlevel 1 goto ready
    timeout /t 1 /nobreak >nul
)

echo.
echo El servidor no respondio en http://127.0.0.1:8080
echo Cierra ventanas anteriores de Mikele Dashboard y vuelve a intentar.
pause
exit /b 1

:ready
echo Abriendo dashboard en http://127.0.0.1:8080
start "" "http://127.0.0.1:8080"
echo.
echo Dashboard listo. Puedes cerrar esta ventana.
echo.
exit /b 0
