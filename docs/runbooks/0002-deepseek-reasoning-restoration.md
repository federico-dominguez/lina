# Runbook 0002 — Restaurar Razonamiento DeepSeek V4 (Thinking Mode)

**Fecha:** 2026-05-26
**Estado:** 🔄 En ejecución
**Servicios afectados:** `lina-goosed.service`, `goose` CLI
**Severidad:** Alta — el parche anterior deshabilitó la capacidad de razonamiento completa del modelo
**Depende de:** Runbook 0001 (contexto del bug original)

---

## 1. Diagnóstico

El parche de 0001 resolvió los errores 400 **de la manera equivocada**: en vez de solo stripear
`reasoning_content` del historial (lo que DeepSeek realmente exige), también **forzó
`thinking: {type: disabled}`** en cada request. Resultado: el modelo corre en modo no-razonante
permanente.

### Contrato real de la API DeepSeek V4 (hybrid thinking)

| Escenario | Regla |
|---|---|
| Thinking **explícitamente enabled** (recomendado) | SIEMPRE pasar `reasoning_content` de vuelta en el siguiente turno |
| Thinking **explícitamente disabled** | NO pasar `reasoning_content` — la API lo rechaza |
| Thinking **no especificado** (modo adaptativo) | ❌ **PELIGROSO**: el modelo decide turno a turno si razona. Genera inconsistencia si un turno pensó y el siguiente no |

**El error original "reasoning_content is not supported when thinking is disabled"** ocurría porque:
1. Thinking no estaba especificado → modo adaptativo
2. El modelo NO pensó en un turno (sin `reasoning_content` en respuesta)
3. Pero Goose tenía `reasoning_content` de un turno ANTERIOR donde sí pensó
4. DeepSeek rechazó ese `reasoning_content` histórico porque el turno actual era "no pensante"

**La solución correcta:** siempre inyectar `thinking: {type: enabled}` explícito para eliminar la
inconsistencia del modo adaptativo. Con thinking siempre ON, `reasoning_content` siempre está
presente y siempre debe pasar de vuelta. Goose's `preserve_thinking_context=true` ya maneja esto.

### Iteraciones del diagnóstico

| Intento | Comportamiento | Error resultante |
|---|---|---|
| Patch 0001 | Forzar disabled + strip history | ✅ sin 400, ❌ sin razonamiento |
| Intento 1 (2026-05-26) | Enabled por defecto + strip history | ❌ "reasoning_content must be passed back" |
| **Solución final** | **Enabled explícito + NO strip** | **✅ sin 400, ✅ razonamiento activo** |

---

## 2. Cambio a aplicar

### 2.1 Archivo

```
~/src/goose/crates/goose/src/providers/openai.rs
```

### 2.2 Lógica del cambio

| | Parche viejo (0001) | Parche nuevo (0002) |
|---|---|---|
| `thinking: disabled` inyectado | ✅ siempre | ❌ nunca (default ON) |
| `reasoning_content` stripeado del historial | ✅ | ✅ (mantener) |
| Modo thinking resultante | No-razonante | Razonante |
| Override por env var `LINA_DEEPSEEK_THINKING` | ❌ | ✅ (`enabled`/`disabled`) |

### 2.3 Tests a actualizar

Los tests en `openai.rs` ~líneas 1020-1140 que verifican que se inyecte `thinking: disabled`
deben invertirse para verificar que:
1. NO se inyecta `thinking: disabled` cuando no hay thinking explícito
2. SÍ se stripea `reasoning_content` del historial
3. Un request con `thinking: {type: disabled}` explícito lo respeta

---

## 3. Plan de ejecución

### Paso 1 — Cambio de código (Dificultad: Baja, Prioridad: P0)

Modificar `sanitize_request_for_compat` en `openai.rs`:
- Quitar la condición `thinking_disabled` del if que controla el inject
- Cambiar comportamiento: si no hay `thinking` key → inyectar `enabled` (o respetar default del API)
- Mantener el strip de `reasoning_content` como comportamiento siempre activo
- Agregar soporte para `LINA_DEEPSEEK_THINKING` como override

### Paso 2 — Tests (Dificultad: Baja, Prioridad: P0)

Actualizar tests para reflejar el nuevo comportamiento correcto.

### Paso 3 — Compilar (Dificultad: Trivial, Prioridad: P0)

```bash
cd ~/src/goose
cargo build --release --bin goose --bin goosed \
    --no-default-features \
    --features portable-default,system-keyring,code-mode
```

### Paso 4 — Mitigación de costo (Dificultad: Trivial, Prioridad: P0)

Reducir `GOOSE_GATEWAY_MAX_TURNS` temporalmente de 200 a 50 hasta tener `cost-meter`.
Thinking activo multiplica 3-8× el costo por turno.

### Paso 5 — Deploy (Dificultad: Trivial, Prioridad: P0)

```bash
systemctl --user stop lina-goosed.service
cp ~/src/goose/target/release/goose ~/.local/bin/goose
cp ~/src/goose/target/release/goosed ~/goosed_patched
systemctl --user start lina-goosed.service
```

### Paso 6 — Smoke test (Dificultad: Trivial, Prioridad: P0)

Enviar 5 mensajes al bot de Telegram y verificar ausencia de errores 400.

### Paso 7 — E2E con razonamiento (Dificultad: Trivial, Prioridad: P0)

Verificar que `reasoning_content` aparece en las respuestas del modelo.

---

## 4. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Tests del fork desactualizados | Invertir assertions antes de compilar |
| Gasto descontrolado por CoT | Reducir GATEWAY_MAX_TURNS a 50 hasta tener cost-meter |
| Latencia alta en triviales | Aceptable por ahora; hot-swap por intent es Roadmap P1 |
| Regresión tras actualización de goose | Agregar test E2E en `tests/e2e/` |

---

## 5. Resultado esperado

- Modelo razona sin errores 400
- `reasoning_content` visible en respuestas
- `LINA_DEEPSEEK_THINKING=disabled` como escape hatch para regresión rápida
- Runbook actualizado con historial de binarios

---

## 6. Historial de ejecución

_(se llena durante la ejecución)_

| Paso | Estado | Notas |
|---|---|---|
| 1. Código | ⏳ | |
| 2. Tests | ⏳ | |
| 3. Compilación | ⏳ | |
| 4. Mitigación costo | ⏳ | |
| 5. Deploy | ⏳ | |
| 6. Smoke test | ⏳ | |
| 7. E2E Telegram | ⏳ | |
