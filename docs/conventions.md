# Convenciones — Quant Engine V2

## Commits (Conventional Commits)

Formato: `<tipo>(<ámbito opcional>): <descripción en minúsculas>`

| Tipo       | Uso                                            |
|------------|------------------------------------------------|
| `feat`     | nueva funcionalidad                            |
| `fix`      | corrección de bug                              |
| `refactor` | cambio interno sin alterar comportamiento      |
| `perf`     | mejora de rendimiento                          |
| `test`     | pruebas                                        |
| `docs`     | documentación                                  |
| `chore`    | mantenimiento (deps, config, CI)               |
| `ci`       | pipelines                                      |

Ejemplos:
```
feat(events): event bus con dead letters y métricas
fix(cache): reintento del primario tras cooldown
docs(adr): ADR-006 cache degradable
```

Reglas: imperativo, ≤ 72 caracteres en el título, cuerpo explicando el *por
qué* cuando no sea obvio. **Nunca commits automáticos**; cada commit es una
decisión humana.

## Ramas

- `main` — estable, protegida por CI.
- `feature/<descripcion>` · `fix/<descripcion>` · `phase/<n>-<nombre>`.

## Código

- Python 3.12, tipado completo, docstrings Google en todo objeto público.
- Módulo = una responsabilidad. Comunicación entre módulos: eventos o
  interfaces de `app/core/interfaces` — nunca imports de implementaciones.
- Prohibido `except:` genérico; usar la jerarquía de `app/core/exceptions`.
- Nada de valores hardcodeados: todo por `app/config`.
- Los tests no tocan red ni servicios reales (ambiente `testing`).

## Herramientas

`ruff check .` · `black .` · `mypy` · `pytest --cov` — todas deben pasar
antes de commitear (y las verifica CI).
