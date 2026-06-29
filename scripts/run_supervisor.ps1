param(
    [string]$PythonPath = ".\.venv\Scripts\python.exe",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5500,
    [string]$BackendHost = "0.0.0.0",
    [int]$HealthIntervalSeconds = 5
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $ProjectRoot "data\runtime\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Start-Backend {
    Write-Host "[supervisor] starting backend on $BackendHost`:$BackendPort"
    return Start-Process -FilePath $PythonPath `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--app-dir", $ProjectRoot, "--host", $BackendHost, "--port", "$BackendPort") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput (Join-Path $LogDir "backend.supervisor.out.log") `
        -RedirectStandardError (Join-Path $LogDir "backend.supervisor.err.log") `
        -WindowStyle Hidden `
        -PassThru
}

function Start-Frontend {
    Write-Host "[supervisor] starting frontend static server on 0.0.0.0:$FrontendPort"
    return Start-Process -FilePath $PythonPath `
        -ArgumentList @("-m", "http.server", "$FrontendPort", "--bind", "0.0.0.0", "--directory", "frontend/static") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput (Join-Path $LogDir "frontend.supervisor.out.log") `
        -RedirectStandardError (Join-Path $LogDir "frontend.supervisor.err.log") `
        -WindowStyle Hidden `
        -PassThru
}

function Test-Backend {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/health" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-Frontend {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$FrontendPort/index.html" -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

$backend = Start-Backend
$frontend = Start-Frontend
Write-Host "[supervisor] running. backend=http://127.0.0.1:$BackendPort frontend=http://127.0.0.1:$FrontendPort"

while ($true) {
    Start-Sleep -Seconds $HealthIntervalSeconds

    if ($backend.HasExited -or -not (Test-Backend)) {
        Write-Host "[supervisor] backend unhealthy, restarting..."
        if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue }
        $backend = Start-Backend
    }

    if ($frontend.HasExited -or -not (Test-Frontend)) {
        Write-Host "[supervisor] frontend unhealthy, restarting..."
        if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue }
        $frontend = Start-Frontend
    }
}
