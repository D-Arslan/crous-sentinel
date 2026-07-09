# Installe CROUS Sentinel comme tache planifiee Windows :
#   - demarre automatiquement a l'ouverture de ta session,
#   - se relance tout seul en cas de plantage,
#   - tourne sans fenetre (via pythonw.exe), logs dans bot.log.
#
# Lancer une seule fois :
#   powershell -ExecutionPolicy Bypass -File "D:\CrousBot\install_task.ps1"

$ErrorActionPreference = "Stop"

$TaskName = "CrousSentinel"
$Dir      = "D:\CrousBot"
$PythonW  = "$Dir\.venv\Scripts\pythonw.exe"
$Script   = "$Dir\bot.py"

if (-not (Test-Path $PythonW)) { throw "Introuvable: $PythonW" }
if (-not (Test-Path $Script))  { throw "Introuvable: $Script" }

# Action : lancer le bot sans fenetre, dans le bon dossier
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Dir

# Declencheur : a l'ouverture de session de l'utilisateur courant
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Reglages fiabilite : relance auto, pas de limite de duree, une seule instance
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# Executer sous l'utilisateur courant, seulement quand il est connecte
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# (Re)cree la tache
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Ancienne tache supprimee."
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "CROUS Sentinel - alerte logement CROUS Val-de-Marne (94)" | Out-Null

Write-Host "Tache '$TaskName' installee." -ForegroundColor Green

# Demarre tout de suite (sans attendre la prochaine ouverture de session)
Start-ScheduledTask -TaskName $TaskName
Write-Host "Bot demarre. Tu devrais recevoir le message Telegram 'demarre'." -ForegroundColor Green
Write-Host "Logs en direct : Get-Content D:\CrousBot\bot.log -Wait -Tail 20"
