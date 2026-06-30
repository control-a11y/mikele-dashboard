$ErrorActionPreference = "Stop"

$startupDir = [Environment]::GetFolderPath("Startup")
$startupLauncher = Join-Path $startupDir "Mikele Dashboard Local.cmd"

if (Test-Path $startupLauncher) {
    Remove-Item $startupLauncher -Force
    Write-Host "Arranque automatico eliminado: $startupLauncher"
} else {
    Write-Host "No habia arranque automatico de usuario instalado."
}
