# Dashboard — Centro de Control (Fase 8)

Dashboard profesional y capa de comandos que convierten el motor en un centro de
operaciones observable y administrable. **Live sigue deshabilitado** (Fase 9):
ninguna acción del dashboard puede habilitarlo (guard anti-live, ADR-060).

## Frontend (`dashboard/`)

App **Next.js 16** (App Router) + React 19 + TypeScript + **TailwindCSS v4** +
**shadcn/ui** (estilo base-nova, Base UI) + **TanStack Query** (REST) + **Zustand**
(estado cliente/UI/workspaces) + **Framer Motion** + **TradingView Lightweight
Charts**. Tema oscuro por defecto, responsive (escritorio + móvil).

### Capa de datos (`src/lib/`)
- `api/client.ts` — fetch tipado (`apiGet`/`apiSend`); un `503` de subsistema
  apagado se expone como `ApiError.notEnabled` (estado "off", no error).
- `api/types.ts` — interfaces TS escritas a mano espejando los `.to_dict()` de
  dominio (el OpenAPI es pobre: las rutas devuelven `dict[str, Any]`).
- `api/hooks.ts` — un hook por endpoint (error fijado a `ApiError`), polling de
  respaldo. `api/mutations.ts` — mutaciones (toast + invalidación).
- `ws/client.ts` + `ws/provider.tsx` — WebSocket resiliente a `/ws/events`
  (backoff+jitter) que alimenta el store, invalida queries por evento y levanta
  alertas/toasts. `store/` — Zustand: `realtime`, `ui` (persistido), `workspace`.
- `components/common/states.tsx` `<Async>` — cada panel maneja loading / error /
  `503 not enabled` / vacío de forma uniforme.

### Pantallas
Overview · Operations · Market (chart + order book) · Strategies · Order Flow ·
Machine Learning · Backtesting · Trade Journal · Logs · Health Center · Reports ·
Alerts · Settings (Config Center). Extras: **Workspace System** (vistas guardadas),
**command palette** (⌘/Ctrl-K) y atajos.

### Correr en local
```bash
cd dashboard
npm install
npm run dev          # http://localhost:3000
# calidad
npm run typecheck && npm run lint && npm test && npm run build
```
Requiere el motor sirviendo la API en `http://localhost:8000`
(`NEXT_PUBLIC_API_BASE` / `NEXT_PUBLIC_WS_URL` en `.env.local`).

## Capa de comandos del backend (`app/dashboard/api/`)

Amplía la API de solo-lectura con escrituras (CORS a `POST/PATCH/PUT/DELETE`,
ADR-059). Toda escritura se **audita** (ADR-061) y pasa por el **guard anti-live**
(ADR-060).

| Recurso | Método | Descripción |
|---|---|---|
| `/api/config` | GET/PATCH | config efectiva (whitelist) / aplicar override |
| `/api/engine/strategies/{name}/enable\|disable` | POST | intención de activación |
| `/api/engine/strategies/{name}/weight` | PATCH | override de peso |
| `/api/backtesting/run` · `/cancel/{job_id}` | POST | acepta y audita (ejecución pesada vía BacktestLab/CLI) |
| `/api/integrations/discord` · `/discord/test` | GET/POST | estado (webhook enmascarado) / mensaje de prueba |
| `/api/integrations/notion` | GET | estado de la integración |
| `/api/logs` | GET | buffer de errores recientes (filtro por nivel) |
| `/api/reports/generate` · `/api/reports` | POST/GET | genera (JSON/MD/CSV) / lista |
| `/api/audit` | GET | traza de auditoría (append-only JSONL) |
| `/api/ml/{predict,train,drift/check,meta/evaluate}` | POST | acciones ML (asesoras) |

Ver `docs/architecture.md` (ADR-058…ADR-062) y "Riesgos conocidos (Fase 8)".
