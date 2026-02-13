# Bastet AI V2 - Launcher PowerShell
# Usage: .\start.ps1

$Host.UI.RawUI.WindowTitle = "Bastet AI V2"

Write-Host ""
Write-Host "========================================"
Write-Host "       BASTET AI V2 - Launcher"
Write-Host "========================================"
Write-Host ""

Set-Location $PSScriptRoot

# Vérifier Python
try {
    python --version | Out-Null
} catch {
    Write-Host "ERREUR: Python n'est pas installé" -ForegroundColor Red
    Read-Host "Appuyez sur Entrée pour fermer"
    exit 1
}

# Vérifier config
if (-not (Test-Path "config.json")) {
    Write-Host "Configuration non trouvée. Lancement du wizard..."
    python config_wizard.py
}

# Démarrer le frontend en arrière-plan
Write-Host "Démarrage du frontend React..." -ForegroundColor Cyan
$frontendJob = Start-Job -ScriptBlock {
    Set-Location $using:PSScriptRoot\web
    npm run dev 2>$null
}

Write-Host "Démarrage du backend Python..." -ForegroundColor Cyan
Write-Host ""
Write-Host "Appuyez sur Ctrl+C pour arrêter proprement." -ForegroundColor Yellow
Write-Host ""

try {
    python main.py
} finally {
    Write-Host ""
    Write-Host "Arrêt en cours..." -ForegroundColor Yellow
    
    # Arrêter le job frontend
    Stop-Job $frontendJob -ErrorAction SilentlyContinue
    Remove-Job $frontendJob -Force -ErrorAction SilentlyContinue
    
    # Tuer les processus node
    Get-Process -Name "node" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    
    Write-Host "Terminé." -ForegroundColor Green
}
