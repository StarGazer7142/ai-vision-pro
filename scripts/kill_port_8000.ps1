$p8 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($p8) {
    $p8 | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        Write-Host "Killed port 8000 PID: $_"
    }
}
Write-Host "Done"
