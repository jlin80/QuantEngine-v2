# Dashboard — preparado (la interfaz se desarrolla en una fase futura)

Stack decidido (no cambiar sin ADR):

- **Next.js** (App Router) + **React** + **TypeScript**
- **TailwindCSS** + **shadcn/ui**
- **TradingView Lightweight Charts** para gráficos de precio

## Contratos ya disponibles en el backend

| Recurso                | Método    | Descripción                              |
|------------------------|-----------|------------------------------------------|
| `/api/health`          | GET       | liveness (app, versión, ambiente)        |
| `/api/system/status`   | GET       | snapshot completo del Health Monitor     |
| `/api/system/info`     | GET       | info estática (instrumentos, fase)       |
| `/ws/events`           | WebSocket | stream JSON de todos los eventos del bus |

Formato de evento (WebSocket): `{"event": "PriceUpdated", "event_id": "...",
"occurred_at": "ISO-8601", "source": "...", ...payload}`.

## Cuando se inicie la fase de dashboard

```bash
npx create-next-app@latest . --typescript --tailwind --app
npx shadcn@latest init
npm install lightweight-charts
```

`docker/dashboard.Dockerfile` y el servicio `dashboard` de `docker-compose.yml`
(perfil `dashboard`) ya están preparados para el build standalone de Next.js.
