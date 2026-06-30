$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$launcher = Join-Path $projectDir "iniciar_dashboard_mikele.bat"
$startupDir = [Environment]::GetFolderPath("Startup")
$startupLauncher = Join-Path $startupDir "Mikele Dashboard Local.cmd"

@"
@echo off
start "" "$launcher"
"@ | Set-Content -Path $startupLauncher -Encoding ASCII

Write-Host "Listo. Mikele Dashboard se iniciara al abrir sesion en Windows."
Write-Host "Archivo creado: $startupLauncher"
