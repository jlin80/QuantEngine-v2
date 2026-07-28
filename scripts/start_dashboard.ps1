# Arranca el dashboard Next.js (build standalone) en :3000.
#
# Idempotente y auto-limpiante: si ya hay una instancia sirviendo, NO arranca
# otra. Antes se lanzaba por tres vias distintas (esta tarea programada, el
# watchdog `run_dashboard_wd.ps1` y `Win32_Process.Create` a mano), y cada
# despliegue dejaba procesos node huerfanos que ademas bloqueaban
# `.next/standalone` y hacian fallar el siguiente `next build` con EBUSY.
#
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1

$ErrorActionPreference = 'SilentlyContinue'

$Root      = Split-Path -Parent $PSScriptRoot
$Dashboard = Join-Path $Root 'dashboard'
$Standalone = Join-Path $Dashboard '.next\standalone'
$LogFile   = Join-Path $Dashboard 'dashboard.log'

$env:Path     = 'C:\Users\MT5\node;' + $env:Path
$env:PORT     = '3000'
$env:HOSTNAME = '0.0.0.0'

function Write-Log($message) {
    $line = "$(Get-Date -Format o)  start_dashboard: $message"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

# --- 1. Ya hay alguien sirviendo? Entonces no duplicamos ------------------
$listening = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Log ("ya hay una instancia en :3000 (PID " + $listening.OwningProcess + "); no se arranca otra")
    exit 0
}

# --- 2. Limpieza de node huerfanos ----------------------------------------
# Un `server.js` que ya no escucha en :3000 es basura de un arranque anterior:
# no sirve a nadie y retiene handles sobre `.next/standalone`.
$orphans = Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'server\.js' }
foreach ($p in $orphans) {
    Write-Log ("matando node huerfano PID " + $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($orphans) { Start-Sleep -Seconds 3 }

# --- 3. Comprobacion del build --------------------------------------------
$server = Join-Path $Standalone 'server.js'
if (-not (Test-Path $server)) {
    Write-Log "FALTA $server - hay que compilar primero: scripts\deploy_dashboard.ps1"
    exit 1
}
# `output: standalone` no copia estos dos: sin ellos la pagina carga sin CSS.
if (-not (Test-Path (Join-Path $Standalone '.next\static'))) {
    Write-Log "AVISO: falta .next/static dentro de standalone; el CSS no cargara"
}

# --- 4. Arranque -----------------------------------------------------------
Write-Log "arrancando node server.js en :3000"
Set-Location $Standalone
& 'C:\Users\MT5\node\node.exe' server.js *>> $LogFile
