# Referencia: Formato Telegram HTML mode

> Archivo de referencia para el formato de respuestas en Telegram.
> Fuente de verdad para cualquier modificación de reglas de formato.
> Última revisión: 2026-05-27

## Contexto

El gateway de Telegram está configurado en **HTML parse mode**.
Markdown puro NO funciona — se renderiza como texto literal con asteriscos y almohadillas.

---

## Etiquetas soportadas por Telegram

| Etiqueta | Resultado |
|---|---|
| `<b>texto</b>` | **negrita** |
| `<i>texto</i>` | _cursiva_ |
| `<code>texto</code>` | `código inline` (monospace) |
| `<pre>bloque</pre>` | bloque de código (monospace multilinea) |
| `<a href="url">texto</a>` | link |
| `<s>texto</s>` | ~~tachado~~ |
| `<u>texto</u>` | subrayado |
| `<tg-spoiler>texto</tg-spoiler>` | spoiler (oculto hasta tap) |

---

## Reglas de longitud

### Límite por mensaje
- **Máximo: 3800 caracteres** por bubble de texto final.
- El límite de la API de Telegram es 4096 chars. Se reservan 296 chars de margen para evitar truncado en bordes de encoding multi-byte.
- Si la respuesta supera 3800 chars, dividirla en múltiples mensajes:
  - Cortar en límites semánticos (párrafo, bloque de código completo).
  - NUNCA cortar en medio de una etiqueta HTML (`<code>...</cod⬤ [corte acá]` → inválido).
  - NUNCA cortar en medio de una lista de bullets.

### Estimación rápida
- Texto plano: ~600–700 words ≈ 3800 chars
- Con tags HTML: ~500–550 words efectivos (las etiquetas ocupan chars)

---

## Estructura de respuestas

### Texto libre (1–2 puntos)
```
Texto plano sin listas. Párrafo directo.
```

### Lista de 3+ ítems
```
• Item uno
• Item dos
• Item tres
```
NO usar `<ul>/<li>` — no soportado por Telegram.

### Con código inline
```
El comando es <code>systemctl status lina-goosed</code> para verificar el estado.
```

### Con bloque de código
```
<pre>
$ just apply-config
$ systemctl --user restart lina-goosed
</pre>
```

### Encabezados de sección
```
<b>Estado del sistema:</b>

Servicio activo, última sesión hace 3 horas.
```
NO usar `# Header` — se muestra como `# Header` literal.

---

## Separadores

NO usar `---` como separador — se renderiza como texto `---`.
Usar doble salto de línea o `<b>Sección</b>` como separador semántico.

---

## Entidades HTML en texto plano

Si el texto contiene `<`, `>`, `&` fuera de etiquetas HTML, deben escaparse:
- `<` → `&lt;`
- `>` → `&gt;`
- `&` → `&amp;`

Ejemplo: `El operador &lt; es para comparar`.

---

## Razonamiento visible

El bloque `💭 Razonando...` que emite Goose:
- Limitarlo a **800 caracteres máximos** de contenido visible.
- Incluir solo la conclusión y los 2–3 puntos clave del razonamiento.
- El resto del proceso de thinking queda en contexto interno.
- No dumpearlo todo como monólogo de 268 bubbles (falla F-6 del 2026-05-27).

---

## Tool calls

Formato estándar:
```
⚙️ Ejecutando: <descripción de 1 línea del propósito>
```

Ejemplos:
```
⚙️ Ejecutando: leer preguntas del cuestionario M2-R1
⚙️ Ejecutando: verificar estado del servicio lina-moodle-mcp
⚙️ Ejecutando: guardar respuesta en bloque de notas
```

NO mostrar el JSON/código del tool call a menos que Federico lo pida.

---

## Patrones a evitar (lista negra)

```
❌ **texto en negrita**          → se muestra literal
❌ *cursiva*                     → se muestra literal  
❌ # Encabezado                  → se muestra literal
❌ ---                           → se muestra literal
❌ | columna | tabla |            → se muestra literal (no hay tablas)
❌ ```code```                    → se muestra literal (usar <pre>)
❌ [link](url)                   → se muestra literal (usar <a href="">)
```

---

## Checklist antes de enviar respuesta larga

- [ ] ¿Supera 3800 chars? → partir
- [ ] ¿Hay Markdown mezclado con HTML? → convertir
- [ ] ¿Todos los `<code>/<pre>/<b>` están correctamente cerrados?
- [ ] ¿Los `<`, `>`, `&` del texto plano están escapados?
- [ ] ¿El razonamiento visible es ≤800 chars?
