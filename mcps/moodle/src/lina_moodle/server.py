"""LINA MCP Moodle — interactúa con cuestionarios de Moodle vía REST API.

Herramientas:
    - moodle_login: Inicia sesión en Moodle.
    - moodle_get_my_courses: Lista los cursos del usuario.
    - moodle_get_course_contents: Obtiene el contenido de un curso.
    - moodle_get_quiz_attempts: Lista intentos de un cuestionario.
    - moodle_start_quiz_attempt: Crea un nuevo intento (NO lo finaliza).
    - moodle_get_quiz_attempt_data: Obtiene preguntas y opciones de un intento ABIERTO.
    - moodle_submit_quiz_answer: Responde una pregunta SIN finalizar el intento.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=os.environ.get("LINA_LOG_LEVEL", "INFO"),
    format="[lina-moodle] %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("lina-moodle")

MOODLE_URL = os.environ.get("MOODLE_URL", "https://ev1.utec.edu.uy/moodle")
MOODLE_USERNAME = os.environ.get("MOODLE_USERNAME", "")
MOODLE_PASSWORD = os.environ.get("MOODLE_PASSWORD", "")
SERVICE_NAME = "moodle_mobile_app"

mcp = FastMCP("lina-moodle")

# Estado de sesión (token)
_auth_token: str | None = None


# ─── Helpers ───────────────────────────────────────────────────────────────────

# Construimos el ampersand con chr() para evitar problemas de escaping XML
_AMP = chr(38)
_LT = chr(60)
_GT = chr(62)
_QUOT = chr(34)

_HTML_ENTITIES = [
    (_AMP + "amp;", _AMP),
    (_AMP + "lt;", _LT),
    (_AMP + "gt;", _GT),
    (_AMP + "quot;", _QUOT),
    ("&#39;", "'"),
    (_AMP + "nbsp;", " "),
    (_AMP + "aacute;", "á"),
    (_AMP + "eacute;", "é"),
    (_AMP + "iacute;", "í"),
    (_AMP + "oacute;", "ó"),
    (_AMP + "uacute;", "ú"),
    (_AMP + "ntilde;", "ñ"),
    (_AMP + "Aacute;", "Á"),
    (_AMP + "Eacute;", "É"),
    (_AMP + "Iacute;", "Í"),
    (_AMP + "Oacute;", "Ó"),
    (_AMP + "Uacute;", "Ú"),
]


def _decode_html(text: str) -> str:
    """Decodifica entidades HTML comunes."""
    if not text:
        return ""
    result = text
    for entity, char in _HTML_ENTITIES:
        result = result.replace(entity, char)
    return result


def _clean_html(html: str, max_len: int = 5000) -> str:
    """Limpia tags HTML y devuelve texto plano."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    text = _decode_html(text)
    return text[:max_len]


def _extract_question_text(html: str) -> str:
    """Extrae el enunciado de la pregunta del HTML de Moodle."""
    match = re.search(
        r'<div[^>]*class="[^"]*qtext[^"]*"[^>]*>(.*?)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        return _decode_html(match.group(1).strip())
    return _clean_html(html, 300)


def _extract_options_from_html(html: str) -> list[dict]:
    """Extrae las opciones de respuesta del HTML de Moodle.

    Retorna lista de dicts: [{"letter": "a", "text": "Opción A"}, ...]
    """
    options: list[dict] = []

    # Estrategia 1: answernumber + flex-fill (Moodle 4.x)
    pattern = (
        r'<span[^>]*class="[^"]*answernumber[^"]*"[^>]*>\s*'
        r'([a-zA-Z])\.'
        r'\s*</span>\s*'
        r'<div[^>]*class="[^"]*flex-fill[^"]*"[^>]*>'
        r'(.*?)'
        r'</div>'
    )
    matches = list(re.finditer(pattern, html, re.DOTALL | re.IGNORECASE))
    if len(matches) >= 2:
        return [
            {"letter": m.group(1).lower(), "text": _decode_html(m.group(2).strip())}
            for m in matches
        ]

    # Estrategia 2: div.r0, div.r1 con label
    r_pattern = (
        r'<div[^>]*class="[^"]*r\d+"[^>]*>'
        r'.*?<label[^>]*>(.*?)</label>\s*</div>'
    )
    r_matches = list(re.finditer(r_pattern, html, re.DOTALL | re.IGNORECASE))
    if len(r_matches) >= 2:
        return [
            {"letter": chr(97 + i), "text": _clean_html(m.group(1), 500)}
            for i, m in enumerate(r_matches)
        ]

    # Estrategia 3: flex-fill solo
    ff_pattern = r'<div[^>]*class="[^"]*flex-fill[^"]*"[^>]*>(.*?)</div>'
    ff_matches = list(re.finditer(ff_pattern, html, re.DOTALL | re.IGNORECASE))
    if len(ff_matches) >= 2:
        return [
            {"letter": chr(97 + i), "text": _decode_html(m.group(1).strip())}
            for i, m in enumerate(ff_matches)
        ]

    return options


def _flatten_params(params: dict) -> dict[str, str]:
    """Aplana params con listas al formato PHP array que espera Moodle.

    {"data": [{"name": "q1:1_answer", "value": "d"}]}
    → {"data[0][name]": "q1:1_answer", "data[0][value]": "d"}
    """
    result: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    for sub_key, sub_value in item.items():
                        result[f"{key}[{i}][{sub_key}]"] = str(sub_value)
                else:
                    result[f"{key}[{i}]"] = str(item)
        else:
            result[key] = str(value)
    return result


async def _moodle_call(function_name: str, params: dict | None = None) -> dict:
    """Llama a la REST API de Moodle."""
    import httpx

    query: dict[str, str] = {
        "wstoken": _auth_token or "",
        "wsfunction": function_name,
        "moodlewsrestformat": "json",
    }
    if params:
        flat = _flatten_params(params)
        query.update(flat)

    url = f"{MOODLE_URL}/webservice/rest/server.php"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, data=query)
        resp.raise_for_status()
        data = resp.json()

    if isinstance(data, dict) and (data.get("exception") or data.get("errorcode")):
        raise RuntimeError(
            f"Moodle API error: {data.get('message') or data.get('errorcode')}"
        )

    return data


async def _login(username: str, password: str) -> str:
    """Obtiene token de autenticación de Moodle."""
    import httpx

    url = f"{MOODLE_URL}/login/token.php"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            data={
                "username": username,
                "password": password,
                "service": SERVICE_NAME,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"Login error: {data['error']}")

    return data["token"]


async def _ensure_auth() -> None:
    """Asegura que haya un token de autenticación."""
    global _auth_token
    if not _auth_token:
        if MOODLE_USERNAME and MOODLE_PASSWORD:
            _auth_token = await _login(MOODLE_USERNAME, MOODLE_PASSWORD)
            log.info("Autenticado automáticamente")
        else:
            raise RuntimeError(
                "No autenticado. Usa moodle_login primero o configura "
                "MOODLE_USERNAME y MOODLE_PASSWORD"
            )


def _log_tool_invocation(tool_name: str, **kwargs: object) -> None:
    """Registra invocación de tool (futuro: emitir ToolInvoked al bus)."""
    record = {"tool": tool_name, "args": {k: str(v) for k, v in kwargs.items()}}
    log.info("ToolInvoked %s", json.dumps(record, ensure_ascii=False))


# ─── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def moodle_login(username: str, password: str) -> str:
    """Inicia sesión en Moodle con usuario y contraseña.
    Necesario antes de usar otras herramientas.

    Args:
        username: Nombre de usuario Moodle
        password: Contraseña Moodle
    """
    global _auth_token
    _log_tool_invocation("moodle_login", username=username)
    _auth_token = await _login(username, password)

    # Verificar sesión
    site_info = await _moodle_call("core_webservice_get_site_info")
    return (
        f"✅ Sesión iniciada como: {site_info.get('fullname', '?')}\n"
        f"Usuario: {site_info.get('username', '?')}\n"
        f"Sitio: {site_info.get('sitename', '?')}\n"
        f"Versión Moodle: {site_info.get('release', '?')}\n"
        f"ID de usuario: {site_info.get('userid', '?')}"
    )


@mcp.tool()
async def moodle_get_my_courses() -> str:
    """Obtiene la lista de cursos del usuario autenticado."""
    await _ensure_auth()
    _log_tool_invocation("moodle_get_my_courses")

    data = await _moodle_call(
        "core_course_get_enrolled_courses_by_timeline_classification",
        {"classification": "all", "limit": 50},
    )
    courses = data.get("courses", data) if isinstance(data, dict) else data

    lines = [f"📚 Cursos encontrados: {len(courses)}\n"]
    for c in courses:
        summary = re.sub(r"<[^>]+>", "", c.get("summary", "") or "")[:150]
        progress = (
            f"{c.get('progress')}%"
            if c.get("progress") is not None
            else "N/A"
        )
        lines.append(
            f"• **{c.get('fullname', '?')}** ({c.get('shortname', '?')})\n"
            f"  ID: {c.get('id')} | Progreso: {progress}"
        )
    return "\n\n".join(lines)


@mcp.tool()
async def moodle_get_course_contents(courseid: int) -> str:
    """Obtiene el contenido/temas de un curso específico.

    Args:
        courseid: ID del curso en Moodle
    """
    await _ensure_auth()
    _log_tool_invocation("moodle_get_course_contents", courseid=courseid)

    sections = await _moodle_call(
        "core_course_get_contents", {"courseid": courseid}
    )

    lines = [f"📖 Contenido del curso ID {courseid}:\n"]
    for s in sections:
        name = s.get("name") or f"Tema {s.get('section', '?')}"
        lines.append(f"**{name}**")
        for mod in s.get("modules", []):
            emoji = {
                "resource": "📄",
                "assign": "📝",
                "quiz": "❓",
                "forum": "💬",
                "url": "🔗",
            }.get(mod.get("modname", ""), "📌")
            lines.append(
                f"  {emoji} **{mod.get('name', '?')}** "
                f"({mod.get('modname', '?')}) [ID: {mod.get('id')}]"
            )
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
async def moodle_get_quiz_attempts(quizid: int) -> str:
    """Obtiene los intentos realizados en un cuestionario.
    Acepta tanto el cmid (view.php?id=XXXX) como el quizid real (instance).

    Args:
        quizid: ID del cuestionario. Puede ser cmid o quizid real.
    """
    await _ensure_auth()
    _log_tool_invocation("moodle_get_quiz_attempts", quizid=quizid)

    real_quiz_id: int = quizid

    # Resolver cmid → instance
    try:
        cm_info = await _moodle_call(
            "core_course_get_course_module", {"cmid": quizid}
        )
        cm_data = cm_info.get("cm", {})
        if cm_data.get("modname") == "quiz" and cm_data.get("instance"):
            real_quiz_id = int(cm_data["instance"])
    except RuntimeError:
        pass

    data = await _moodle_call(
        "mod_quiz_get_user_attempts",
        {"quizid": real_quiz_id, "userid": 0, "status": "all"},
    )

    attempts = data.get("attempts", [])
    if not attempts:
        return f"📝 No hay intentos registrados para el cuestionario ID {quizid}."

    lines = [f"📝 Intentos del cuestionario ID {quizid}:\n"]
    for a in attempts:
        state_emoji = (
            "🔓"
            if a.get("state") == "inprogress"
            else "🔒"
            if a.get("state") == "finished"
            else "⚪"
        )
        lines.append(
            f"• {state_emoji} **Intento #{a.get('attempt')}** (ID: {a.get('id')})\n"
            f"  Estado: {a.get('state')} | "
            f"Calificación: {a.get('sumgrades', 'N/A')}"
        )
    return "\n\n".join(lines)


@mcp.tool()
async def moodle_start_quiz_attempt(quizid: int) -> str:
    """Crea/empieza un nuevo intento en un cuestionario.
    Acepta tanto el cmid (ID del módulo en la URL: view.php?id=XXXX)
    como el quizid real (instance). Resuelve automáticamente cmid → quizid.
    Retorna información del intento creado incluyendo las preguntas
    y opciones disponibles. NO finaliza el intento, solo lo inicia.

    Args:
        quizid: ID del cuestionario. Puede ser:
                - cmid: el número en /mod/quiz/view.php?id=XXXX (course module id)
                - quizid: el instance real de la tabla quiz
    """
    await _ensure_auth()
    _log_tool_invocation("moodle_start_quiz_attempt", quizid=quizid)

    real_quiz_id: int = quizid

    # Intentar resolver como cmid → instance (quizid real)
    try:
        cm_info = await _moodle_call(
            "core_course_get_course_module", {"cmid": quizid}
        )
        cm_data = cm_info.get("cm", {})
        if cm_data.get("modname") == "quiz" and cm_data.get("instance"):
            real_quiz_id = int(cm_data["instance"])
            log.info(
                "Resuelto cmid=%d → quiz instance=%d (%s)",
                quizid, real_quiz_id, cm_data.get("name", "?"),
            )
    except RuntimeError:
        # No es un cmid, asumimos que ya es un quizid real
        log.info("Usando quizid=%d directamente (no resuelto como cmid)", quizid)

    # Verificar si ya hay un intento en progreso
    try:
        existing = await _moodle_call(
            "mod_quiz_get_user_attempts",
            {"quizid": real_quiz_id, "userid": 0, "status": "all"},
        )
        in_progress = [
            a for a in existing.get("attempts", [])
            if a.get("state") == "inprogress"
        ]
        if in_progress:
            a = in_progress[0]
            return (
                f"⚠️ **Ya existe un intento en progreso** para este cuestionario.\n\n"
                f"🆔 Attempt ID: **{a['id']}**\n"
                f"📝 Intento #: {a.get('attempt', 'N/A')}\n"
                f"📊 Estado: {a.get('state')}\n"
                f"📅 Inicio: {a.get('timestart', 'N/A')}\n\n"
                f"Para ver las preguntas usa moodle_get_quiz_attempt_data(attemptid={a['id']})\n"
                f"Para responder usa moodle_submit_quiz_answer(attemptid={a['id']}, ...)\n\n"
                f"⚠️ Si quieres un NUEVO intento, cierra primero el actual en la web de Moodle."
            )
    except RuntimeError:
        pass  # Si falla get_user_attempts, continuamos con start_attempt

    data = await _moodle_call("mod_quiz_start_attempt", {"quizid": real_quiz_id})

    attempt = data.get("attempt", {})
    questions = data.get("questions", [])

    lines = [
        "🚀 **NUEVO INTENTO INICIADO**\n",
        f"📝 Intento ID: **{attempt.get('id')}**",
        f"📝 Intento #: {attempt.get('attempt', 'N/A')}",
        f"📊 Estado: {attempt.get('state', 'inprogress')}",
        f"📅 Inicio: {attempt.get('timestart', 'N/A')}",
        "",
        "---",
        "",
        f"## 📋 Preguntas ({len(questions)})\n",
    ]

    for i, q in enumerate(questions):
        html = q.get("html", "")
        question_text = _extract_question_text(html)
        options = _extract_options_from_html(html)
        slot = i + 1

        lines.append(
            f"### Pregunta {slot} [Slot: {slot}] "
            f"[Tipo: {q.get('type', 'unknown')}]"
        )
        lines.append(f"📖 **Enunciado:** {question_text}")

        if options:
            lines.append("📋 **Opciones:**")
            for opt in options:
                lines.append(f"  {opt['letter']}) {opt['text']}")
        else:
            lines.append("📋 **Opciones:** (pregunta de texto libre u otro formato)")

        lines.append(f"📊 Puntaje máximo: {q.get('maxmark', 'N/A')}")
        lines.append("")

    lines.append("---")
    lines.append("⚠️ **IMPORTANTE:** Este intento está ABIERTO (inprogress).")
    lines.append("Usa moodle_submit_quiz_answer para responder cada pregunta.")
    lines.append("NO se cerrará automáticamente.")

    return "\n".join(lines)


@mcp.tool()
async def moodle_get_quiz_attempt_data(attemptid: int) -> str:
    """Obtiene las preguntas y opciones de un intento de cuestionario ABIERTO.
    Funciona con intentos 'inprogress' y NO cierra ni finaliza el intento.
    Retorna todas las preguntas con enunciados, opciones y respuestas guardadas.

    Args:
        attemptid: ID del intento abierto (en progreso)
    """
    await _ensure_auth()
    _log_tool_invocation("moodle_get_quiz_attempt_data", attemptid=attemptid)

    # Leer página 0 primero para obtener el layout y saber cuántas páginas hay
    page0_data = await _moodle_call(
        "mod_quiz_get_attempt_data",
        {"attemptid": attemptid, "page": 0},
    )
    attempt = page0_data.get("attempt", {})
    all_questions: list[dict] = list(page0_data.get("questions", []))

    # layout: string separado por comas, "0" separa páginas.
    # Ej: "1,2,0,3,0" → 2 páginas: [1,2] y [3]
    # Ej: "18,0,8,0,..." → cada slot en su propia página
    layout_str = attempt.get("layout", "")
    total_pages = 1  # al menos página 0
    if layout_str:
        # Dividir el layout en páginas usando "0" como separador
        layout_parts = [p.strip() for p in layout_str.split(",")]
        pages_list: list[list[str]] = []
        current_page: list[str] = []
        for part in layout_parts:
            if part == "0":
                if current_page:
                    pages_list.append(current_page)
                    current_page = []
            else:
                current_page.append(part)
        if current_page:
            pages_list.append(current_page)
        total_pages = max(len(pages_list), 1)
        total_slots = sum(len(p) for p in pages_list)
        log.info(
            "Layout: %d slots en %d páginas → %s",
            total_slots, total_pages,
            " | ".join(f"p{p_i}: {slots}" for p_i, slots in enumerate(pages_list)),
        )
    else:
        log.info("Layout: no disponible, asumiendo 1 página")

    # Extraer el resto de páginas
    for page in range(1, total_pages):
        try:
            data = await _moodle_call(
                "mod_quiz_get_attempt_data",
                {"attemptid": attemptid, "page": page},
            )
            questions = data.get("questions", [])
            if not questions:
                break
            all_questions.extend(questions)
        except RuntimeError:
            log.warning("Error en página %d: skipping", page)
            break

    lines = [
        f"📋 **DATOS DEL INTENTO ABIERTO #{attempt.get('attempt', '?')}**\n",
        f"🆔 Attempt ID: **{attempt.get('id')}**",
        f"📊 Estado: {attempt.get('state', 'inprogress')}",
        f"📅 Inicio: {attempt.get('timestart', 'N/A')}",
        f"⏱️ Tiempo límite: {attempt.get('timelimit', 'Sin límite')}",
        f"📝 Preguntas totales: {len(all_questions)}",
        "",
        "---",
        "",
        "## 📋 Preguntas y Opciones\n",
    ]

    for i, q in enumerate(all_questions):
        html = q.get("html", "")
        question_text = _extract_question_text(html)
        options = _extract_options_from_html(html)
        slot = i + 1
        qno_match = re.search(r'<span class="qno">(\d+)</span>', html)
        qno = qno_match.group(1) if qno_match else str(slot)

        lines.append(
            f"### Pregunta {qno}/{len(all_questions)} "
            f"[Slot: {q.get('slot', slot)}] "
            f"[Tipo: {q.get('type', 'unknown')}]"
        )
        lines.append(f"📖 **Enunciado:** {question_text}")

        if options:
            lines.append("📋 **Opciones:**")
            for opt in options:
                lines.append(f"  {opt['letter']}) {opt['text']}")
        else:
            lines.append("📋 **Opciones:** (pregunta de texto libre u otro formato)")

        # Detectar respuesta guardada desde JSON o desde checked en HTML
        current_answer = q.get("currentAnswer") or q.get("response") or ""
        if not current_answer or current_answer in ("", "-1"):
            # Buscar radio checked en el HTML
            checked_match = re.search(
                r'<input[^>]*type="radio"[^>]*value="(\d+)"[^>]*checked',
                html,
            )
            if checked_match:
                numeric_val = int(checked_match.group(1))
                letter_val = {0: "a", 1: "b", 2: "c", 3: "d"}.get(
                    numeric_val, str(numeric_val)
                )
                current_answer = f"{letter_val} (idx={numeric_val})"
        if current_answer and current_answer != "" and current_answer != "-1":
            lines.append(f"✏️ Respuesta guardada: {current_answer}")

        if q.get("flagged"):
            lines.append("🚩 Marcada para revisión")

        lines.append(f"📊 Puntaje máximo: {q.get('maxmark', 'N/A')}")
        lines.append("")

    lines.append("---")
    lines.append("⚠️ **IMPORTANTE:** Este intento está ABIERTO y NO se ha cerrado.")
    lines.append(
        "Usa moodle_submit_quiz_answer(attemptid, slot, answer) "
        "para responder preguntas SIN finalizar el intento."
    )
    lines.append("Para finalizar, debes hacerlo manualmente en Moodle.")

    return "\n".join(lines)


@mcp.tool()
async def moodle_submit_quiz_answer(
    attemptid: int,
    slot: int,
    answer: str,
) -> str:
    """Envía la respuesta a una pregunta dentro de un intento en progreso.
    NO finaliza el intento; solo guarda la respuesta para el slot indicado.
    Usa save_attempt en vez de process_attempt para evitar cerrar el intento.

    Args:
        attemptid: ID del intento en progreso
        slot: Número de slot/pregunta. Es el slot REAL de Moodle,
              no el índice secuencial (ej: 18, 8, 17...).
        answer: Letra de la respuesta: a, b, c, d.
    """
    import re as _re

    await _ensure_auth()
    _log_tool_invocation(
        "moodle_submit_quiz_answer",
        attemptid=attemptid,
        slot=slot,
        answer=answer,
    )

    # Mapear letra → índice numérico (a→0, b→1, c→2, d→3)
    _letter_to_idx = {"a": 0, "b": 1, "c": 2, "d": 3}
    answer_lower = answer.strip().lower()
    numeric_answer = _letter_to_idx.get(answer_lower)
    if numeric_answer is None:
        return f"❌ Respuesta inválida: '{answer}'. Usa a, b, c, o d."

    # 1. Buscar el slot en todas las páginas del intento
    qubaid = None
    sequencecheck = "1"
    max_page = 20  # límite de seguridad
    for page in range(max_page):
        attempt_data = await _moodle_call(
            "mod_quiz_get_attempt_data",
            {"attemptid": attemptid, "page": page},
        )
        questions = attempt_data.get("questions", [])
        if not questions:
            break  # no hay más páginas
        found = False
        for q in questions:
            if q.get("slot") == slot:
                html = q.get("html", "")
                qubaid_match = _re.search(r"qubaid=(\d+)", html)
                if qubaid_match:
                    qubaid = qubaid_match.group(1)
                seq_match = _re.search(
                    rf'q\d+:{slot}_:sequencecheck"\s+value="(\d+)"', html
                )
                if seq_match:
                    sequencecheck = seq_match.group(1)
                found = True
                break
        if found:
            break

    if not qubaid:
        return (
            f"❌ No se encontró el slot {slot} en el intento {attemptid}. "
            "Verifica el número de slot real."
        )

    # 2. Guardar respuesta con el formato real de Moodle (incluye sequencecheck)
    result = await _moodle_call(
        "mod_quiz_save_attempt",
        {
            "attemptid": attemptid,
            "data": [
                {"name": f"q{qubaid}:{slot}_answer", "value": str(numeric_answer)},
                {"name": f"q{qubaid}:{slot}_:sequencecheck", "value": sequencecheck},
            ],
        },
    )

    status = result.get("status", False)
    warnings = result.get("warnings", [])

    lines = [
        "✅ **RESPUESTA GUARDADA**\n",
        f"🆔 Attempt ID: {attemptid}",
        f"🔢 Slot: {slot}",
        f"📝 Respuesta: {answer_lower} (opción #{numeric_answer})",
        f"📊 Estado: {'OK' if status else 'FALLÓ'}",
    ]

    if warnings:
        for w in warnings:
            lines.append(f"⚠️ Advertencia: {w.get('message', str(w))}")

    if status:
        lines.append("\n✅ Respuesta guardada correctamente.")
    else:
        lines.append("\n❌ No se pudo guardar la respuesta.")

    lines.append("\n⚠️ El intento sigue ABIERTO (no finalizado).")
    lines.append("Puedes seguir respondiendo más preguntas con esta misma tool.")

    return "\n".join(lines)


@mcp.tool()
async def moodle_finish_quiz_attempt(attemptid: int) -> str:
    """Finaliza un intento de cuestionario en progreso.
    Envía finishattempt=1 a mod_quiz_process_attempt para cerrar el intento.
    Después de esto, se puede obtener la revisión con mod_quiz_get_attempt_review.

    Args:
        attemptid: ID del intento a finalizar
    """
    await _ensure_auth()
    _log_tool_invocation("moodle_finish_quiz_attempt", attemptid=attemptid)

    # Finalizar el intento — solo necesita attemptid + finishattempt=1
    result = await _moodle_call(
        "mod_quiz_process_attempt",
        {
            "attemptid": attemptid,
            "finishattempt": 1,
        },
    )

    state = result.get("state", "unknown")
    warnings = result.get("warnings", [])

    # Obtener revisión post-finalización
    try:
        review = await _moodle_call(
            "mod_quiz_get_attempt_review",
            {"attemptid": attemptid},
        )
        rev_attempt = review.get("attempt", {})
        questions = review.get("questions", [])

        correct = sum(1 for q in questions if q.get("status") in ("correct", "Correct"))
        incorrect = sum(1 for q in questions if q.get("status") in ("incorrect", "Incorrect"))
        partial = sum(1 for q in questions if q.get("status") in ("partiallycorrect", "PartiallyCorrect"))
        total = len(questions)
        score = rev_attempt.get("sumgrades", "N/A")
        grade = rev_attempt.get("grade", "N/A")

        lines = [
            "🚀 **INTENTO FINALIZADO**\n",
            f"🆔 Attempt ID: **{attemptid}**",
            f"📊 Estado: finished",
            f"📝 Total preguntas: {total}",
            f"✅ Correctas: {correct}/{total}",
            f"❌ Incorrectas: {incorrect}/{total}",
            f"🟡 Parciales: {partial}/{total}",
            f"📈 Puntaje: **{score}**",
        ]

        if warnings:
            for w in warnings:
                lines.append(f"⚠️ Advertencia: {w.get('message', str(w))}")

        return "\n".join(lines)
    except RuntimeError as e:
        # Si la revisión no está disponible, al menos devolver confirmación
        return (
            f"🚀 **INTENTO FINALIZADO**\n\n"
            f"🆔 Attempt ID: **{attemptid}**\n"
            f"📊 Estado: finished\n"
            f"⚠️ Revisión no disponible: {e}"
        )


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Punto de entrada del MCP server."""
    log.info("starting lina-moodle (url=%s)", MOODLE_URL)

    # Login automático si hay credenciales en entorno
    import asyncio

    if MOODLE_USERNAME and MOODLE_PASSWORD:
        try:
            asyncio.get_event_loop().run_until_complete(
                _login(MOODLE_USERNAME, MOODLE_PASSWORD)
            )
        except Exception as e:
            log.warning("Login automático falló: %s", e)

    mcp.run()


if __name__ == "__main__":
    main()