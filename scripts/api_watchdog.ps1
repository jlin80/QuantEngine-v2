# Vigila que la API del motor siga respondiendo, y avisa por Discord si no.
#
# Nace de un caso real (13/08/2026): el proceso estaba vivo, el puerto 8000
# escuchando y el motor operando con normalidad -- pero `/api/health` no
# respondia ni en 90 segundos. Un reinicio lo resolvio y nunca supimos por que.
#
# Por que no lo cubria nada de lo que ya habia: las alarmas de pipeline miran el
# ratio de descarte del validador ("ciego") y la sequia de senales ("mudo"). Con
# la API colgada y el motor operando, NINGUNA de las dos dispara -- el motor
# estaba haciendo su trabajo. Y el watchdog de componentes vigila el proceso,
# que tampoco se habia caido. El hueco es exactamente este: la API como servicio
# observable por si misma.
#
# Deliberadamente NO reinicia nada. Un reinicio automatico habria borrado la
# unica evidencia del incidente del 13/08, y una API colgada no pierde dinero
# por si misma -- el motor sigue operando. Avisa y deja decidir.
#
# Uso (tarea programada cada 5 min):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\api_watchdog.ps1

$ErrorActionPreference = 'Continue'

$Root       = Split-Path -Parent $PSScriptRoot
$Url        = 'http://127.0.0.1:8000/api/health'
$TimeoutSec = 30
$StateFile  = Join-Path $Root 'logs\api_watchdog.state'

function Get-WebhookUrl {
    # Se lee del .env en cada pasada: nunca hardcodeado, y una rotacion del
    # webhook no obliga a tocar este fichero.
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*QE_DISCORD__WEBHOOK_URL\s*=\s*(.+?)\s*$') {
            return $Matches[1].Trim('"').Trim("'")
        }
    }
    return $null
}

function Send-Discord($title, $description, $color) {
    $webhook = Get-WebhookUrl
    if (-not $webhook) { Write-Output 'sin webhook configurado'; return }
    $payload = @{
        embeds = @(@{
            title       = $title
            description = $description
            color       = $color
            footer      = @{ text = 'api_watchdog' }
            timestamp   = (Get-Date).ToUniversalTime().ToString('o')
        })
    } | ConvertTo-Json -Depth 6
    try {
        Invoke-RestMethod -Uri $webhook -Method Post -ContentType 'application/json' `
            -Body $payload -TimeoutSec 20 | Out-Null
    } catch {
        Write-Output ("no se pudo avisar por Discord: " + $_.Exception.Message)
    }
}

# Latch: sin esto la alarma se repetiria cada 5 minutos, y una alarma que se
# repite se silencia sola. Se avisa en la TRANSICION, y tambien al recuperarse
# -- saber que ya esta bien importa tanto como saber que se rompio.
$previous = if (Test-Path $StateFile) { (Get-Content $StateFile -Raw).Trim() } else { 'ok' }

$healthy = $false
$detail  = ''
try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    $healthy  = ($response.StatusCode -eq 200)
    $detail   = "HTTP $($response.StatusCode)"
} catch {
    $detail = $_.Exception.Message
}

$current = if ($healthy) { 'ok' } else { 'down' }
Write-Output ("api=$current  ($detail)")

if ($current -ne $previous) {
    if ($current -eq 'down') {
        # El estado de los procesos va en el aviso: distingue "se cayo" de
        # "sigue vivo pero no responde", que son incidentes muy distintos.
        $engines = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -match 'QuantEngineV2' -and $_.CommandLine -match '-m app' }).Count
        $listening = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).Count
        Send-Discord "API del motor sin responder" (
            "``/api/health`` no responde en $TimeoutSec s.`n" +
            "Detalle: $detail`n" +
            "Procesos del motor: $engines`n" +
            "Puerto 8000 escuchando: $listening`n`n" +
            "El motor puede seguir operando: esto vigila la API, no el trading. " +
            "**Mira ``logs\app.log`` ANTES de reiniciar** -- un reinicio borra la evidencia."
        ) 15158332
    } else {
        Send-Discord "API del motor recuperada" "``/api/health`` vuelve a responder ($detail)." 3066993
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path $StateFile) | Out-Null
Set-Content -Path $StateFile -Value $current -Encoding utf8
if (-not $healthy) { exit 1 }
