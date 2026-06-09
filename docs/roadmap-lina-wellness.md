# 🌸 LINA Wellness Roadmap

> *"El gran propósito de LINA es saber que estás feliz."*

---

## 🎯 Visión

Transformar a `s_lina_bot` de un asistente genérico en una **compañera de equilibrio, salud y bienestar** para Fede. LINA conoce a Fede, lo acompaña día a día, lo ayuda a mantenerse equilibrado, sano, enfocado y feliz.

---

## 🗺️ Roadmap Visual

```
 SEMANA 1-2        SEMANA 2-3         SEMANA 3-5         SEMANA 4-6         SEMANA 5-8
 (09-20 Jun)       (16-23 Jun)        (23 Jun-07 Jul)    (30 Jun-14 Jul)    (07-28 Jul)
                                                                                
 ┌──────────┐     ┌──────────┐       ┌──────────┐       ┌──────────┐       ┌────────────┐
 │ #201     │     │ #203     │       │ #204     │       │ #206     │       │ #207 😊    │
 │ System   │────→│ User     │──→    │ Health & │──→    │ Proactive│────→  │ Happiness  │
 │ Prompt   │     │ Profile  │       │ Habits   │       │ Scheduler│       │ Index      │
 └──────────┘     └──────────┘       └──────────┘       └──────────┘       └────────────┘
      │                │                                        │                 │
      ▼                ▼                                        ▼                 ▼
 ┌──────────┐     ┌──────────┐                              ┌──────────┐    ┌────────────┐
 │ #202     │     │ #205     │                              │ #208     │    │ TODOS los  │
 │ Wheel of │     │ Daily    │                              │ Calend.  │    │ MCPs       │
 │ Life MCP │     │ Check-in │                              │ Proact.  │    │ integrados │
 └──────────┘     └──────────┘                              └──────────┘    └────────────┘

🎯 HITO 1          🎯 HITO 2           🎯 HITO 3           🎯 HITO 4         🎯 HITO 5
LINA tiene        LINA conoce         LINA cuida la       LINA busca        LINA mide la
personalidad      a Fede              salud de Fede       a Fede            felicidad
```

---

## 📋 Issues del Roadmap

| # | Issue | Tipo | Timeline | Dependencias |
|---|---|---|---|---|
| [#201](https://github.com/federico-dominguez/lina/issues/201) | 🌸 System Prompt Terapéutico | `enhancement` | Sem 1 (09-13 Jun) | — |
| [#202](https://github.com/federico-dominguez/lina/issues/202) | 🎡 Wheel of Life MCP | `new-mcp` | Sem 1-2 (09-20 Jun) | #201 |
| [#203](https://github.com/federico-dominguez/lina/issues/203) | 👤 User Profile MCP | `new-mcp` | Sem 2-3 (16-23 Jun) | #201 |
| [#204](https://github.com/federico-dominguez/lina/issues/204) | 🩺 Health & Habits Tracker | `new-mcp` | Sem 3-5 (23 Jun-07 Jul) | — |
| [#205](https://github.com/federico-dominguez/lina/issues/205) | 🌅 Daily Check-in + 🌙 Cierre | `enhancement` | Sem 2-3 (16-23 Jun) | #201, #203 |
| [#206](https://github.com/federico-dominguez/lina/issues/206) | 🔔 Proactive Scheduler | `enhancement` | Sem 4-6 (30 Jun-14 Jul) | #205, #202, #204 |
| [#207](https://github.com/federico-dominguez/lina/issues/207) | 😊 Happiness Index | `enhancement` | Sem 6-8 (14-28 Jul) | #202, #204, #205 |
| [#208](https://github.com/federico-dominguez/lina/issues/208) | 📅 Calendario Proactivo | `enhancement` | Sem 5-7 (07-21 Jul) | #201, #206 |

---

## 🏗️ Nuevos MCPs a crear

| MCP | Puerto | Tabla DB |
|---|---|---|
| `lina-wheel-of-life` | 8113 | `lina.wheel_of_life` |
| `lina-user-profile` | 8114 | `lina.user_profile` |
| `lina-health-tracker` | 8115 | `lina.health_log` |

---

## 🎯 Hitos

### 🥇 Hito 1 — LINA tiene personalidad (Sem 1-2)
- System prompt terapéutico activo
- Wheel of Life funcional con `/rueda`
- LINA habla con tono cálido, estoico y rioplatense

### 🥈 Hito 2 — LINA conoce a Fede (Sem 2-3)
- Perfil de Fede almacenado
- Check-in matutino diario
- Cierre nocturno reflexivo

### 🥉 Hito 3 — LINA cuida la salud (Sem 3-5)
- Tracking de agua, comidas, movimiento, sueño, ánimo
- LINA detecta patrones sin perseguir

### 🏅 Hito 4 — LINA busca a Fede (Sem 4-6)
- Mensajes proactivos programados
- Alertas por desequilibrios detectados
- Calendario revisado automáticamente

### 🏆 Hito 5 — LINA mide la felicidad (Sem 6-8)
- Happiness Index funcional
- Tendencia semanal visible
- LINA comenta evoluciones positivas y áreas de mejora

---

## 🚫 Lo que LINA NO es

| ❌ No | ✅ Sí |
|---|---|
| Un tracker frío | Una presencia que pregunta cómo te sentís |
| Una app que regaña | Una aliada que sugiere sin juzgar |
| Un bot que solo responde | Una compañera que también inicia |
| Reemplazo de terapia | Complemento estoico entre sesiones |
| Máquina de productividad | Guardiana de tu equilibrio |

---

*Roadmap creado el 2026-06-09. Última actualización: [fecha].*
