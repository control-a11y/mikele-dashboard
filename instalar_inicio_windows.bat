@echo off
setlocal
cd /d "%~dp0"

set "TASK_NAME=Mikele Dashboard Local"
set "DASHBOARD_CMD=%~dp0iniciar_dashboard_mikele.bat"

echo Instalando arranque automatico de Mikele Dashboard...
schtasks /Create /SC ONLOGON /TN "%TASK_NAME%" /TR "\"%DASHBOARD_CMD%\"" /F

if errorlevel 1 (
    echo.
    echo No se pudo crear la tarea automatica.
    echo Ejecuta este archivo como administrador si Windows lo solicita.
    pause
    exit /b 1
)

echo.
echo Listo. El dashboard se iniciara automaticamente al iniciar sesion en Windows.
echo Tambien puedes abrirlo manualmente con iniciar_dashboard_mikele.bat.
pause
