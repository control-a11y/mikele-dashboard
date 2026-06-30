@echo off
setlocal

set "TASK_NAME=Mikele Dashboard Local"

echo Quitando arranque automatico de Mikele Dashboard...
schtasks /Delete /TN "%TASK_NAME%" /F

echo.
echo Si la tarea existia, ya fue eliminada.
pause
