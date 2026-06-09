"""
pipeline_orchestrator — Pipeline inteligente sobre issues reales del repo.

Cada paso recibe prompts diseñados para su rol específico.
Se comunican via Telegram (visible en el grupo Comm) y GitHub Issues (documentación).
"""

import json, time, os, sys, re, subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_bot_bridge import send as tg_send

REPO = "federico-dominguez/lina"
WORKSPACE = "/home/user/lina"
STOP_FLAG = "/tmp/pipeline_stop"

# ─── Response cleaner ───────────────────────────────────────────────────────

def _clean_response(text: str) -> str:
    """Extrae solo el resumen final de la respuesta del bot."""
    # Si tiene --- RESUMEN ---, tomar SOLO eso
    if "--- RESUMEN ---" in text:
        parts = text.split("--- RESUMEN ---")
        return parts[-1].strip()[:3000] if parts[-1].strip() else parts[0].strip()[:3000]
    
    # Si tiene --- RESUMEN (sin ---), probar igual
    if "RESUMEN" in text.upper():
        for marker in ["RESUMEN:", "RESUMEN\n", "## Resumen", "**Resumen**"]:
            if marker in text:
                return text.split(marker)[-1].strip()[:3000]
    
    # Fallback: limpiar thinking/tool calls
    lines = text.split("\n")
    clean = []
    skip = False
    for line in lines:
        if any(x in line for x in ["Razonando", "razonando", "💭"]):
            continue
        if line.strip().startswith(("✅ **", "⚙️")) or "📥 Input" in line or "📤 Output" in line:
            skip = True; continue
        if skip and "```" in line:
            skip = False; continue
        if skip: continue
        if not clean and not line.strip(): continue
        clean.append(line)
    
    result = "\n".join(clean).strip()
    return result if len(result) > 100 else text[:2000]


# ─── GitHub helpers ─────────────────────────────────────────────────────────

def _gh(*args, input_text=""):
    cmd = ["gh", "-R", REPO] + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, input=input_text)
    if r.returncode != 0:
        print(f"  ⚠️ gh: {r.stderr.strip()[:200]}")
    return r.stdout.strip()

def issue_fetch(num):
    """Fetch full issue details as dict."""
    return json.loads(_gh("issue", "view", str(num), "--json", "number,title,body,labels,state,comments,assignees,url"))

def issue_comment(num, body):
    _gh("issue", "comment", str(num), "--body", body)

def issue_close(num):
    _gh("issue", "close", str(num))

def issue_create(title, body):
    out = _gh("issue", "create", "--title", title, "--body", body)
    m = re.search(r'/issues/(\d+)$', out.strip())
    return int(m.group(1)) if m else 0


# ─── Graceful stop ──────────────────────────────────────────────────────────

def _check_stop():
    if Path(STOP_FLAG).exists():
        msg = open(STOP_FLAG).read().strip()
        print(f"\n  🛑 {msg}")
        os.unlink(STOP_FLAG)
        return True
    return False


# ─── Adaptive step resolver ─────────────────────────────────────────────────

def resolve_steps(issue):
    """Decide qué pasos ejecutar según los labels del issue.
    
    Returns: list de dicts con {name, bot, prompt, timeout}
    """
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]
    labels_str = " ".join(labels)
    issue_num = issue.get("number", "?")
    
    # ── Exclude labels (only if they are the ONLY relevant label) ──
    has_explicit = any(l in labels_str for l in ["bug", "memory", "enhancement", "refactor", "docs", "infra", "security"])
    for exclude in ["analysis", "design", "pipeline-skip"]:
        if exclude in labels:
            print(f"  ⏭️ Issue con label '{exclude}' — skip pipeline")
            return []
    if "architecture" in labels and not has_explicit:
        print(f"  ⏭️ Issue solo con label 'architecture' — skip pipeline")
        return []
    
    # ── Helper: step factory ──
    def step(name, bot, timeout, extra=""):
        prompts = {
            "Analyze": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: TECH LEAD. Creá un plan de implementación preciso.

📋 Tarea:
1. Leé el issue completo
2. Explorá SOLO 1-2 archivos clave (no más)
3. Creá un plan paso a paso con: archivos a modificar, dependencias, tests

CRÍTICO: 2 archivos máximo. Si ya entendés, producí el plan directo.

Terminá con:
---
RESUMEN: 3 líneas con lo más importante.""",
            "Research": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: RESEARCH ENGINEER. Investigá para la implementación.

📋 Tarea:
1. Leé issue y comentario de LINA
2. Buscá SOLO info NUEVA que LINA no haya considerado
3. Si no hay nada nuevo: decí "Sin hallazgos adicionales"

Terminá con:
---
RESUMEN: 3 líneas o "Sin hallazgos adicionales".""",
            "Dev": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: DEVELOPER. Implementá la solución con estándar profesional.

REGLAS ESTRICTAS:
1. Branch: `fix/issue-{issue_num}-<slug>` o `feat/issue-{issue_num}-<slug>`
2. Commits: conventional (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`)
3. Tests: `python3 -m pytest tests/ -x` ANTES de push
4. Push: después de tests verdes → creá PR draft
5. CI: debe pasar antes de merge

Pasos:
1. `git checkout -b feat/issue-{issue_num}-<descripcion-corta>`
2. Implementar + tests
3. `python3 -m pytest tests/ -x` → si fallan, corregí
4. `git add -A && git commit -m "feat: descripción"`
5. `git push origin HEAD`
6. Crear PR con:`gh pr create --fill --draft`

Terminá con:
---
RESUMEN: branch | archivos | tests pasan (SÍ/NO)""",
            "Review": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: CODE REVIEWER. Revisá la implementación de Cline.

📋 Tarea:
1. Determiná la branch del issue
2. Revisá: `git diff main...HEAD`
3. Verificá: código correcto, tests, edge cases
4. ✅ APROBÁ o ❌ RECHAZÁ con razones concretas

Terminá con:
---
RESUMEN: ✅ Aprobado / ❌ Rechazado + razón.""",
            "Test": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: QA. Validá la implementación rigurosamente.

📋 Tarea:
1. Checkout de la branch
2. `python3 -m pytest tests/ -v` — suite completa
3. Si ALGÚN test falla → ❌ FAIL

Terminá con:
---
RESUMEN: ✅ tests OK / ❌ FAIL + cuáles fallaron.""",
            "Doc": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: DOCS. Documentá los cambios realizados.

📋 Tarea:
1. Checkout de la branch
2. Si cambios en API/estructura: actualizá ADR o README
3. Si aplica: CHANGELOG.md

Terminá con:
---
RESUMEN: docs actualizados o "Sin cambios".""",
            "Analysis": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: ANALYST. Verificá la calidad general de la implementación.

📋 Tarea:
1. Revisá que todos los requisitos del issue estén cubiertos
2. Verificá cobertura de tests
3. ¿Hay edge cases no cubiertos?
4. ✅ APROBÁ / 🔄 REITERAR con cambios necesarios

Terminá con:
---
RESUMEN: ✅ Aprobado / 🔄 Reiterar + cambios.""",
            "E2E": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: QA E2E. Ejecutá los tests end-to-end reales via Telegram.

📋 Tarea:
1. Corré los e2e tests: `python3 -m pytest tests/e2e/telegram/scenarios/test_pipeline_e2e.py -v --timeout=600 2>&1`
2. Corré los e2e tests de knowledge: `python3 -m pytest tests/e2e/telegram/scenarios/test_knowledge_e2e.py -v --timeout=120 2>&1`
3. Reportá: cuántos tests pasaron, cuántos fallaron, métricas de latencia

CRÍTICO: Si los e2e tests fallan → reportá exactamente qué test y por qué.

Terminá con:
---
RESUMEN: ✅ E2E OK (X/Y tests) / ❌ E2E FAIL (detalles)""",
            "E2E": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: QA E2E. Ejecutá los tests end-to-end reales via Telegram.

📋 Tarea:
1. Corré los e2e tests: `python3 -m pytest tests/e2e/telegram/scenarios/test_pipeline_e2e.py -v --timeout=600 2>&1`
2. Corré los e2e tests de knowledge: `python3 -m pytest tests/e2e/telegram/scenarios/test_knowledge_e2e.py -v --timeout=120 2>&1`
3. Reportá: cuántos tests pasaron, cuántos fallaron, métricas de latencia

CRÍTICO: Si los e2e tests fallan → reportá exactamente qué test y por qué.

Terminá con:
---
RESUMEN: ✅ E2E OK (X/Y tests) / ❌ E2E FAIL (detalles)""",

            "Decision": f"""Revisá el issue #{issue_num} en github.com/federico-dominguez/lina/issues/{issue_num}
Tu rol: TECH LEAD DECISION. Basado en Analysis, Review y Test:
✅ COMPLETE → cerrar issue.
🔄 REITERATE → volver a Dev con cambios específicos.

Decisión final. Terminá con:
---
DECISION: ✅ COMPLETE / 🔄 REITERATE"""
        }
        p = prompts.get(name, extra)
        return {"name": name, "bot": bot, "timeout": timeout, "prompt": p}
    
    # ── Base steps by label ──
    steps_map = {
        "bug":       [step("Analyze","lina",600), step("Dev","cline",1200), step("Review","gemma",600), step("Test","cline",600), step("E2E","cline",900)],
                    "memory":    [step("Analyze","lina",900), step("Dev","cline",1800), step("Review","gemma",600), step("Test","cline",600), step("E2E","cline",900), step("Analysis","goose",600), step("Decision","lina",300)],
        "refactor":  [step("Analyze","lina",600), step("Dev","cline",1200), step("Review","gemma",600), step("Test","cline",600), step("E2E","cline",900)],
        "enhancement": [step("Analyze","lina",600), step("Research","gemma",600), step("Dev","cline",1200), step("Review","gemma",600), step("Test","cline",600), step("E2E","cline",900)],
        "docs":      [step("Dev","cline",600), step("Doc","cline",300)],
        "infra":     [step("Analyze","lina",600), step("Dev","cline",1200), step("Test","cline",600)],
        "security":  [step("Analyze","lina",600), step("Research","gemma",600), step("Dev","cline",1200), step("Review","gemma",600), step("Test","cline",600)],
    }
    
    for label_key, steps in steps_map.items():
        if label_key in labels_str:
            print(f"  📋 Label '{label_key}' → {len(steps)} pasos")
            return steps
    
    # Default: analyze → dev → test
    return steps_map["bug"]


def extract_branch(text):
    m = re.search(r'pipeline/[\w-]+', text)
    return m.group(0) if m else ""


# ─── Main ──────────────────────────────────────────────────────────────────

def run_pipeline(feature_or_issue):
    logs = []
    t0 = time.time()
    result = {"feature": "", "branch": "", "passed": False, "duration": 0, "issue": 0, "issue_title": ""}

    def log(msg):
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)
        logs.append(msg)

    log(f"🚀 Pipeline iniciado")

    # ── Resolve issue ───────────────────────────────────────────────────
    try:
        if str(feature_or_issue).isdigit():
            issue_num = int(feature_or_issue)
            issue = issue_fetch(issue_num)
            issue_title = issue.get("title", "")
            issue_body = issue.get("body", "")
            log(f"🐙 Issue #{issue_num}: {issue_title}")
        else:
            issue_title = feature_or_issue[:80]
            issue_body = f"## Feature Request\n\n{feature_or_issue}\n\n---\n### Pipeline Checklist\n- [ ] 🔍 Analyzed\n- [ ] 🔬 Researched\n- [ ] 🛠️ Developed\n- [ ] 🧪 Tested\n- [ ] ✅ Completed"
            issue_num = issue_create(f"Pipeline: {issue_title}", issue_body)
            log(f"🐙 Issue #{issue_num} creado")
    except Exception as e:
        log(f"❌ Error creando issue: {e}")
        return result

    result["issue"] = issue_num
    result["issue_title"] = issue_title
    result["feature"] = issue_title

    # ── Resolve steps adaptively ──
    steps = resolve_steps(issue)
    if not steps:
        log("⏭️ Pipeline saltado por configuración de labels")
        result["passed"] = True
        return result

    log(f"📋 Steps: {' → '.join(s['name'] for s in steps)}\n")

    # ── Execute steps dynamically ──
    step_outputs = {}
    try:
        for step in steps:
            sname = step["name"]
            bot = step["bot"]
            timeout = step["timeout"]
            prompt = step["prompt"]

            log(f"{'✅' if step_outputs else '▶️'}  {sname} ({bot})...")

            # Build context from previous steps
            context = f"Issue #{issue_num}: {issue_title}\n\n{prompt}"
            if "analyze" in sname.lower():
                context += f"\n\nIssue description:\n{issue_body[:500]}"

            resp = tg_send(bot, context, timeout=timeout, last_n=5)
            step_outputs[sname] = resp

            log(f"  {sname}: {len(resp)} chars")
            if resp:
                icons = {"Analyze":"🔍","Research":"🔬","Dev":"🛠️","Review":"🔎","Test":"🧪","Doc":"📄"}
                issue_comment(issue_num, f"## {icons.get(sname,'📋')} {sname}\n\n{_clean_response(resp)}")

            result["branch"] = result["branch"] or extract_branch(resp)

            if _check_stop():
                raise SystemExit("🛑 Detenido por usuario")

        # Determine pass/fail from test output
        test_out = step_outputs.get("Test", "")
        has_pass = "FAIL" not in test_out.upper()[:200]
        has_fail = "❌ FAIL" in test_out or ("FAIL" in test_out.upper()[:100] and "NO" not in test_out.upper()[:100])
        result["passed"] = has_pass and not has_fail

        # ── Close issue ──
        summary = (
            f"## ✅ Pipeline completada\n\n"
            f"**Estado:** {'✅ PASÓ' if result['passed'] else '❌ FALLÓ'}\n"
            f"**Branch:** {result['branch'] or 'N/A'}\n"
            f"**Duración:** {time.time() - t0:.0f}s\n"
            f"**Issue:** #{issue_num} — https://github.com/{REPO}/issues/{issue_num}"
        )
        issue_comment(issue_num, summary)
        has_wip = "wip" in [l.get("name","").lower() for l in issue.get("labels",[])]
        if not has_wip:
            issue_close(issue_num)
            log("🐙 Issue cerrado")
        else:
            log("🐙 Issue NO cerrado (label wip)")

        result["duration"] = time.time() - t0
        status = "✅ PASÓ" if result["passed"] else "❌ FALLÓ"
        log(f"\n{'='*60}")
        log(f"📊 Pipeline: {status}")
        log(f"⏱️  {result['duration']:.0f}s | 🐙 #{issue_num}")
        log(f"🌿 {result['branch'] or 'N/A'}")
        log(f"{'='*60}")

    except SystemExit:
        result["duration"] = time.time() - t0
        log(f"\n🛑 Pipeline detenida ({result['duration']:.0f}s)")
    except Exception as e:
        result["duration"] = time.time() - t0
        log(f"❌ Error: {e}")
        import traceback; log(traceback.format_exc())

    return result


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    
    if len(args) >= 2 and args[0] == "--issue":
        feat = args[1]
    else:
        feat = " ".join(args) or "Crear un endpoint /health que verifique PostgreSQL"
    
    print(f"\n🚀 Pipeline GitHub + Telegram: '{feat}'\n")
    r = run_pipeline(feat)
    print(f"\n📊 Resultado: {'✅ PASÓ' if r['passed'] else '❌ FALLÓ'} ({r['duration']:.0f}s)")
    print(f"🐙 https://github.com/{REPO}/issues/{r.get('issue', '?')}")
    sys.exit(0 if r["passed"] else 1)
