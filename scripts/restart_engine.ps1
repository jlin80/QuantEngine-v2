# Reinicia el motor QuantEngine V2 sin dejar watchdogs duplicados.
#
# `Stop-ScheduledTask` no siempre mata el arbol de procesos de la tarea: cada
# ciclo Stop/Start podia dejar vivo el `run_v2.ps1` anterior, y se acumulaban
# varios watchdogs compitiendo por relanzar el mismo motor (se llegaron a ver
# dos a la vez). Este script verifica y limpia en vez de asumir.
#
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart_engine.ps1

$ErrorActionPreference = 'Continue'

$Root     = Split-Path -Parent $PSScriptRoot
$TaskName = 'QuantEngineV2'

# Ojo: PowerShell "desenrolla" los arrays al devolverlos, asi que una funcion
# que devuelve @() vacio devuelve $null y `.Count` sale vacio en vez de 0. Por
# eso se envuelve con @(...) en cada LLAMADA, no solo dentro de la funcion.
function Watchdogs { Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -match 'run_v2\.ps1' } }
function Engines { Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'QuantEngineV2' -and $_.CommandLine -match '-m app' } }

Write-Output ("Antes  -> watchdogs: " + @(Watchdogs).Count + "  motores: " + @(Engines).Count)

Write-Output "`n=== Parando la tarea $TaskName ==="
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

# El watchdog relanza el motor a los 10 s, asi que hay que matarlo A EL primero;
# si no, matar el motor solo consigue que lo reviva.
Write-Output "=== Limpiando watchdogs supervivientes ==="
foreach ($p in @(Watchdogs)) {
    Write-Output ("  matando watchdog PID " + $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3

Write-Output "=== Limpiando motores supervivientes ==="
foreach ($p in @(Engines)) {
    Write-Output ("  matando motor PID " + $p.ProcessId)
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3

if (@(Watchdogs).Count -gt 0 -or @(Engines).Count -gt 0) {
    Write-Output "ABORTADO: quedan procesos vivos; revisar a mano antes de arrancar"
    exit 1
}

Write-Output "`n=== Arrancando ==="
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 50

Write-Output ("Despues -> watchdogs: " + @(Watchdogs).Count + "  motores: " + @(Engines).Count + "  (debe ser 1 y 1)")
try {
    $code = (Invoke-WebRequest 'http://localhost:8000/api/health' -UseBasicParsing -TimeoutSec 20).StatusCode
    Write-Output ("API health -> " + $code)
} catch {
    Write-Output ("API NO responde: " + $_.Exception.Message)
    exit 1
}
