# Telegram E2E Tests — Setup

## Requisitos

1. **Cuenta de Telegram de testing** (número dedicado, no tu cuenta personal).
2. **API credentials** de [my.telegram.org](https://my.telegram.org).
3. **Lina corriendo** (local via Docker o staging).

## Variables de entorno necesarias

```bash
export TELEGRAM_TEST_API_ID=12345678
export TELEGRAM_TEST_API_HASH=abcdef1234567890abcdef1234567890
export TELEGRAM_TEST_PHONE=+59812345678   # cuenta de testing
export LINA_BOT_USERNAME=@TuLinaTestBot

# Opcionales
export LINA_E2E_SESSION_DIR=/ruta/a/.sessions   # default: tests/e2e/telegram/.sessions
export LINA_E2E_COLLECT_TIMEOUT=90              # segundos máx para esperar respuesta
export LINA_E2E_STABLE_WINDOW=3.0               # segundos de inactividad = respuesta completa
export LINA_E2E_ENV=staging                     # documentativo, no tiene efecto directo
```

Gestioná los secretos via `lina-secrets` — **nunca** los pongas en `.env` del repo.

## Instalación de dependencias

```bash
cd /home/fede/lina
pip install -r tests/e2e/telegram/requirements-e2e.txt
# o con uv:
uv pip install -r tests/e2e/telegram/requirements-e2e.txt
```

## Primera ejecución (autenticación Telethon)

La primera vez que corras cualquier test o el CLI, Telethon va a pedir
autenticación interactiva (código SMS o Telegram app). La sesión queda
guardada en `.sessions/lina_e2e.session` (gitignored).

```bash
bin/lina-tg-probe send "Hola"
# → Te pedirá el código de verificación la primera vez
```

## Correr los tests

```bash
# Suite completa
pytest -m e2e_telegram tests/e2e/telegram/ -v

# Solo formatting
pytest -m e2e_telegram tests/e2e/telegram/ -k formatting -v

# Solo streaming (latencias)
pytest -m e2e_telegram tests/e2e/telegram/ -k streaming -v
```

## CLI lina-tg-probe

```bash
# Modo single-shot
bin/lina-tg-probe send "¿Cuánto es 2+2?"
bin/lina-tg-probe send "Dame código Python" --json

# Modo interactivo
bin/lina-tg-probe repl

# Correr suite desde CLI
bin/lina-tg-probe suite --filter formatting
bin/lina-tg-probe suite --report json
```

## Interpretación de métricas

| Métrica | Descripción | Target |
|---|---|---|
| TTFT | Time to First Token: primer mensaje del bot | < 5s |
| TTLT | Time to Last Token: última edición | < 60s (respuesta normal) |
| Edits | Cantidad de `editMessageText` | proporcional al largo |
| ~TPS | Tokens/segundo aproximado (chars/4/ttlt) | > 5 TPS |
| Edit intervals | Milisegundos entre edits consecutivos | 100ms–3000ms |

## Archivos gitignored

- `.sessions/` — sesiones Telethon (contienen tokens de acceso)
- `.report.json` — reporte JSON generado por pytest-json-report
