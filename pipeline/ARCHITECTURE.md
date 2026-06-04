# Pipeline Multi-Grupo — Arquitectura

## Visión General

Múltiples grupos de Telegram trabajando en paralelo sobre issues distintos.
Cada grupo tiene su propio pipeline, su propia sesión de Telethon, y su propio issue.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MANAGER CENTRAL                              │
│              (manager.py — monitorea y coordina)                     │
└────┬──────────────┬──────────────┬──────────────┬───────────────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ Worker A │ │ Worker B │ │ Worker C │ │ Worker D │
│ Grupo A  │ │ Grupo B  │ │ Grupo C  │ │ Grupo D  │
│ Issue #N │ │ Issue #M │ │ Issue #P │ │ Issue #Q │
│ tel.session│ │ tel.session│ │ tel.session│ │ tel.session│
│ logs/a.log│ │ logs/b.log│ │ logs/c.log│ │ logs/d.log│
└─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘
      │            │            │            │
      └────────────┼────────────┼────────────┘
                   ▼            ▼
            ┌────────────┐ ┌────────────┐
            │ @s_lina_bot│ │ @s_cline   │
            │ @s_gemma   │ │ (agentes   │
            │ @s_goose   │ │ compartidos)│
            └────────────┘ └────────────┘
```

## Componentes

### 1. Manager Central (`manager.py`)
- Lee `groups.yaml` con definición de grupos
- Spawnea un worker process por grupo
- Monitorea health de cada worker (heartbeat cada 30s)
- Reinicia workers caídos (opcional)
- Logs centralizados: `logs/manager.log`

### 2. Workers (cada uno corre `pipeline_orchestrator.py`)
- Cada worker es un proceso Python independiente
- Tiene su propia sesión de Telethon (`sessions/group_*.session`)
- Tiene su propio log (`logs/group_*.log`)
- Reporta su estado al manager via archivo de estado (`state/group_*.json`)
- Envía comandos a los mismos agentes (LINA, Cline, Gemma)
- Cada agente procesa requests secuencialmente (cola natural del gateway)

### 3. Agentes Compartidos
- LINA, Cline, Gemma son los mismos para todos los grupos
- Los gateways Telegram procesan un mensaje a la vez
- Los pipelines no interfieren porque cada grupo usa @mentions distintos
- El workspace (repo) se comparte pero cada pipeline trabaja en su propia branch

### 4. Grupos de Telegram
- Cada grupo tiene un nombre único (ej: "Comm A 🩷", "Comm B 🩷")
- Cada grupo tiene su propia sesión de Telethon
- Los mensajes de cada pipeline aparecen en su grupo correspondiente
- El usuario puede monitorear todos los grupos desde el celular

## Flujo de Trabajo

```
1. Manager lee groups.yaml
2. Para cada grupo:
   a. Asigna un issue disponible (del repo, sin asignar)
   b. Crea un worker process
   c. Worker ejecuta pipeline_orchestrator.py --issue N --group "Comm A"
3. Worker corre Analyze → Research → Dev → Test
4. Worker notifica al manager cuando termina
5. Manager asigna nuevo issue al worker
```

## groups.yaml

```yaml
groups:
  - name: "Comm A"
    session: "sessions/group_a.session"
    issue: 113  # o "auto" para tomar el próximo issue disponible
    auto_next: true  # tomar nuevo issue al terminar

  - name: "Comm B"  
    session: "sessions/group_b.session"
    issue: "auto"
    auto_next: true

  - name: "Comm C"
    session: "sessions/group_c.session"
    issue: "auto"
    auto_next: true
```

## Docker Isolation

Para máxima independencia, cada worker puede correr en su propio contenedor:

```yaml
# docker-compose.pipeline.yml
services:
  worker-a:
    build: 
      context: ..
      dockerfile: deploy/docker/Dockerfile.pipeline
    environment:
      PIPELINE_GROUP: "Comm A"
      PIPELINE_ISSUE: "113"
    volumes:
      - pipeline-sessions-a:/app/sessions
    network_mode: host

  worker-b:
    build: 
      context: ..
      dockerfile: deploy/docker/Dockerfile.pipeline
    environment:
      PIPELINE_GROUP: "Comm B"  
      PIPELINE_ISSUE: "91"
    volumes:
      - pipeline-sessions-b:/app/sessions
    network_mode: host
```

## Estados

| Estado | Significado |
|--------|-------------|
| `idle` | Esperando asignación |
| `analyzing` | LINA analizando |
| `researching` | Gemma investigando |
| `developing` | Cline desarrollando |
| `testing` | Gemma testeando |
| `completed` | Pipeline terminó |
| `failed` | Pipeline falló |
| `stopped` | Detenido por usuario |

## Comandos

```bash
./manager.sh start          # Inicia todos los grupos
./manager.sh stop           # Detiene todos los grupos
./manager.sh status         # Estado de todos los workers
./manager.sh logs worker-a  # Logs de un worker específico
./manager.sh add-group "Comm D" --issue 42  # Agregar grupo en caliente
```
