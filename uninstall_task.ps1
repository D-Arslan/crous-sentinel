# Arrete et supprime la tache planifiee de CROUS Sentinel.
#   powershell -ExecutionPolicy Bypass -File .\uninstall_task.ps1

$TaskName = "CrousSentinel"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tache '$TaskName' arretee et supprimee." -ForegroundColor Green
} else {
    Write-Host "Aucune tache '$TaskName' trouvee."
}

# Coupe aussi un eventuel bot deja lance (pythonw qui execute bot.py)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*bot.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Processus bot (PID $($_.ProcessId)) arrete."
    }
