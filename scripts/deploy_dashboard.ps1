# Recompila y redespliega el dashboard Next.js sin dejar basura.
#
# El `next build` fallaba con `EBUSY: rmdir '.next\standalone'` una y otra vez.
# La causa NO eran handles residuales de Windows: es que la tarea programada
# `QuantEngineV2Dashboard` corre un watchdog (`run_dashboard_wd.ps1`) que
# relanza `server.js` a los 10 s de matarlo - volvia a bloquear el directorio
# justo mientras el build intentaba borrarlo. Por eso aqui se para la TAREA,
# no solo el proceso.
#
# Uso:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\deploy_dashboard.ps1

$ErrorActionPreference = 'Continue'

$Root       = Split-Path -Parent $PSScriptRoot
$Dashboard  = Join-Path $Root 'dashboard'
$Standalone = Join-Path $Dashboard '.next\standalone'
$TaskName   = 'QuantEngineV2Dashboard'

$env:Path = 'C:\Users\MT5\node;' + $env:Path

function Step($message) { Write-Output ("`n=== " + $message + " ===") }

Step "Parando el watchdog del dashboard ($TaskName)"
# Primero la tarea: si solo matamos node, el watchdog lo relanza en 10 s y el
# build vuelve a chocar con EBUSY.
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

Step "Matando procesos node de server.js"
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'server\.js' } |
    ForEach-Object {
        Write-Output ("  PID " + $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 4
$left = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'server\.js' }).Count
if ($left -gt 0) {
    Write-Output "  ABORTADO: siguen vivos $left procesos node; el build fallaria con EBUSY"
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    exit 1
}

Step "Borrando el standalone anterior"
# Se regenera entero en cada build; borrarlo evita mezclar restos de builds
# viejos (el `next build` solo hace rmdir, no limpia lo que copiamos encima).
Remove-Item $Standalone -Recurse -Force -ErrorAction SilentlyContinue

Set-Location $Dashboard

Step "Typecheck"
& npx --no-install tsc --noEmit
if ($LASTEXITCODE -ne 0) {
    Write-Output "  tsc fallo: no se despliega"
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    exit 1
}

Step "next build"
& npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Output "  build fallo: no se despliega"
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    exit 1
}

Step "Copiando assets dentro del standalone"
# `output: standalone` no copia `.next/static` ni `public`: sin esto la pagina
# carga sin CSS ni imagenes.
Copy-Item (Join-Path $Dashboard '.next\static') (Join-Path $Standalone '.next\static') -Recurse -Force
if (Test-Path (Join-Path $Dashboard 'public')) {
    Copy-Item (Join-Path $Dashboard 'public') (Join-Path $Standalone 'public') -Recurse -Force
}

Step "Relanzando el watchdog"
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 15

Step "Verificacion"
try {
    $code = (Invoke-WebRequest 'http://localhost:3000/' -UseBasicParsing -TimeoutSec 20).StatusCode
    Write-Output ("  dashboard / -> " + $code)
} catch {
    Write-Output ("  dashboard NO responde: " + $_.Exception.Message)
    exit 1
}
$nodes = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'server\.js' }).Count
Write-Output ("  procesos node sirviendo: " + $nodes + "  (debe ser 1)")
