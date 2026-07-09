# Installe CROUS Sentinel comme tache planifiee Windows :
#   - demarre a l'ouverture de session,
#   - se re-verifie toutes les 10 min et redemarre SEULEMENT si plus aucune
#     instance ne tourne (auto-reparation SANS doublon, grace a IgnoreNew),
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

# Purge de securite : tue toute instance bot.py deja lancee (evite les doublons)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*bot.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Instance existante arretee (PID $($_.ProcessId))."
    }

# Action : lancer le bot sans fenetre, dans le bon dossier
$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`"" -WorkingDirectory $Dir

# Declencheurs :
#  1) a l'ouverture de session
#  2) toutes les 10 min (auto-reparation ; ignore si une instance tourne deja)
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Premier passage dans 10 min (pas maintenant) pour ne pas entrer en course
# avec le Start-ScheduledTask explicite plus bas.
$trigEvery = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(10)) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

# Reglages : UNE seule instance a la fois (IgnoreNew = pas de doublon),
# pas de limite de duree. Pas de RestartCount (le bot gere deja ses erreurs
# en interne et ne plante pas ; RestartCount provoquait des doublons).
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

# Executer sous l'utilisateur courant, seulement quand il est connecte
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# (Re)cree la tache
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Ancienne tache supprimee."
}
Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger $trigLogon, $trigEvery `
    -Settings $settings -Principal $principal `
    -Description "CROUS Sentinel - alerte logement CROUS Val-de-Marne (94)" | Out-Null

Write-Host "Tache '$TaskName' installee." -ForegroundColor Green

# Demarre tout de suite (une seule instance grace a IgnoreNew)
Start-ScheduledTask -TaskName $TaskName
Write-Host "Bot demarre. Tu devrais recevoir le message Telegram 'demarre'." -ForegroundColor Green
Write-Host "Logs en direct : Get-Content D:\CrousBot\bot.log -Wait -Tail 20"
