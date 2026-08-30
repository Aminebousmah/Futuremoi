<#
    Veille quotidienne : campagne de collecte puis rapport HTML.

    Ne genere volontairement AUCUNE candidature : la selection des offres
    auxquelles postuler reste un geste manuel (`radar apply`).

    Planification quotidienne a 8h :
      schtasks /create /tn "freelance-radar" ^
        /tr "powershell -File C:\chemin\vers\scripts\veille.ps1" /sc daily /st 08:00
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$radar = Join-Path $root ".venv\Scripts\radar.exe"

if (-not (Test-Path $radar)) {
    Write-Error "radar introuvable : $radar — lancez d'abord 'pip install -e .' dans le venv."
}

$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("veille-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') — campagne ===" | Out-File $log -Append -Encoding utf8
& $radar scrape 2>&1 | Tee-Object -FilePath $log -Append
& $radar report -f html 2>&1 | Tee-Object -FilePath $log -Append

$rapport = Join-Path $root "output\rapport.html"
if (Test-Path $rapport) {
    Write-Host "Rapport disponible : $rapport"
}
