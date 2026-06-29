param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistDirName = "dist",
    [string]$PackagePrefix = "AI_Video_Platform_Delivery"
)

$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Remove-IgnoredItems {
    param(
        [string]$RootPath,
        [string[]]$ExcludedDirNames,
        [string[]]$ExcludedFilePatterns,
        [string[]]$ExcludedFileNames
    )

    Get-ChildItem -LiteralPath $RootPath -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $ExcludedDirNames -contains $_.Name } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }

    foreach ($pattern in $ExcludedFilePatterns) {
        Get-ChildItem -LiteralPath $RootPath -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            }
    }

    if ($ExcludedFileNames.Count -gt 0) {
        Get-ChildItem -LiteralPath $RootPath -Recurse -Force -File -ErrorAction SilentlyContinue |
            Where-Object { $ExcludedFileNames -contains $_.Name } |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
            }
    }

    Get-ChildItem -LiteralPath $RootPath -Recurse -Force -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like ".env.*" -and $_.Name -ne ".env.example" } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
}

$projectPath = (Resolve-Path $ProjectRoot).Path
$distRoot = Join-Path $projectPath $DistDirName

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$packageName = "$PackagePrefix`_$timestamp"
$stageDir = Join-Path $distRoot $packageName
$zipPath = Join-Path $distRoot "$packageName.zip"

$excludedDirNames = @(
    ".git",
    ".venv",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist"
)
$excludedFilePatterns = @("*.pyc", "*.pyo", "*.log")
$excludedFileNames = @(".env", ".env.local")

Write-Info "Project root: $projectPath"
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

if (Test-Path $stageDir) {
    Remove-Item -LiteralPath $stageDir -Recurse -Force
}
if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

Write-Info "Copying project files to staging directory..."
$rootItems = Get-ChildItem -LiteralPath $projectPath -Force
foreach ($item in $rootItems) {
    if ($excludedDirNames -contains $item.Name) {
        continue
    }
    Copy-Item -LiteralPath $item.FullName -Destination $stageDir -Recurse -Force
}

Write-Info "Removing ignored folders and cache files in staging..."
Remove-IgnoredItems -RootPath $stageDir -ExcludedDirNames $excludedDirNames -ExcludedFilePatterns $excludedFilePatterns -ExcludedFileNames $excludedFileNames

Write-Info "Writing delivery note..."
$deliveryNotePath = Join-Path $stageDir "DELIVERY_NOTE.txt"
@"
Build Time: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Quick Start:
1. Run setup_env.bat
2. Run start_delivery.bat
3. Open http://127.0.0.1:5500/index.html

Detailed guide:
- docs\组员电脑部署与运行说明.md
"@ | Set-Content -LiteralPath $deliveryNotePath -Encoding UTF8

Write-Info "Creating zip package..."
Compress-Archive -Path $stageDir -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host ""
Write-Host "==========================================="
Write-Host "Package generated successfully."
Write-Host "Staging Folder: $stageDir"
Write-Host "Zip File:       $zipPath"
Write-Host "==========================================="
