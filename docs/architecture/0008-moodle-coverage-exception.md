# ADR-0008 — Excepción de cobertura para lina-moodle

**Estado:** Aceptado  
**Fecha:** 2026-05-29  
**Contexto:** Fase 0 Foundations (Issue #13, PR #18)

---

## Decisión

El MCP `lina-moodle` tiene un umbral de cobertura de **18 %** en CI,
mientras que los demás MCPs mantienen el gate de **≥ 60 %** establecido
en Issue #13.

---

## Motivación

`lina-moodle` implementa un cliente HTTP para la API REST de Moodle
(Autenticación, Quiz, Attempt, etc.). El cuerpo del servidor (`server.py`,
≈ 345 líneas) está compuesto casi íntegramente por:

1. **Llamadas HTTP con `httpx`** — requieren una instancia real de Moodle o
   mocks de red complejos para cada endpoint.
2. **Lógica de formato de respuesta** — depende de las estructuras JSON que
   devuelve la API, cuya variación real es difícil de cubrir sin fixtures
   exhaustivas.
3. **Flujos de autenticación con token de sesión** — el ciclo de login/logout
   exige credenciales válidas de UTEC Moodle, no disponibles en CI.

Las únicas funciones actualmente testeables sin credenciales son las
utilitarias de parseo HTML (`_decode_html`, `_clean_html`,
`_extract_question_text`) y la configuración de entorno (`MOODLE_URL`),
que representan el ~ 19 % de cobertura logrado.

---

## Alternativas descartadas

| Alternativa | Por qué se descartó |
|---|---|
| Mock de `httpx` con `respx` | Requeriría fixtures para cada uno de los ~15 endpoints Moodle; mantenimiento alto y bajo valor real |
| Instancia Moodle en Docker en CI | Tiempo de setup > 5 min; la imagen oficial de Moodle requiere MySQL + configuración inicial; supera el scope de Fase 0 |
| Subir umbral a 60 % con tests superficiales | Falso positivo: cobertura sintética sin valor de regresión |

---

## Plan de mejora

En **Fase 2** (integración Moodle avanzada), se elevará la cobertura mediante:

- Fixtures de respuesta HTTP con `respx` para los endpoints principales
  (`core_webservice_get_site_info`, `mod_quiz_*`)
- Tests de integración contra una instancia Moodle en Docker (job separado,
  opcional con `workflow_dispatch`)

Cuando la cobertura supere el 60 % se actualizará el `cov_threshold` en el
CI matrix y se marcará esta ADR como supersedida por la ADR correspondiente.

---

## Consecuencias

- **Positivo:** CI no falla por una limitación estructural de la capa HTTP.
- **Positivo:** La excepción es explícita, documentada y revisable.
- **Negativo:** El núcleo HTTP de moodle no tiene cobertura de regresión
  hasta Fase 2.
- **Mitigación:** El E2E de stdio verifica que `lina-moodle` arranca y expone
  sus herramientas correctamente (test de smoke).
