# Degree Flow — bootstrap + run (Windows / PowerShell).
# Cria a venv, instala dependências, builda o front (se preciso) e sobe o servidor
# que serve API + interface juntos em http://localhost:8000
param(
    [switch]$Rebuild,   # força rebuild do front
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Push-Location $root
try {
    $py = Join-Path $root "backend\.venv\Scripts\python.exe"

    # 1) Backend venv + deps
    if (-not (Test-Path $py)) {
        Write-Host "==> Criando virtualenv do backend..." -ForegroundColor Cyan
        python -m venv (Join-Path $root "backend\.venv")
    }
    Write-Host "==> Instalando dependências Python..." -ForegroundColor Cyan
    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r (Join-Path $root "backend\requirements.txt")

    # 2) Frontend build
    $dist = Join-Path $root "frontend\dist"
    if ($Rebuild -or -not (Test-Path $dist)) {
        Write-Host "==> Buildando o frontend (npm ci + build)..." -ForegroundColor Cyan
        Push-Location (Join-Path $root "frontend")
        npm ci
        npm run build
        Pop-Location
    }

    # 3) Run (serve API + SPA)
    Write-Host "==> Servidor no ar em http://localhost:$Port  (Ctrl+C para parar)" -ForegroundColor Green
    & $py -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
