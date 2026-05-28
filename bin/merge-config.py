#!/usr/bin/env python3
"""Merge helper: copia gateway_pairings/gateway_pending_codes del config antiguo al nuevo.

Uso: python merge-config.py <new_config.yaml> <old_config.yaml>
Edita new_config.yaml in-place.

IMPORTANTE: las secciones gateway se copian como TEXTO PLANO para evitar que
pyyaml (YAML 1.1) corrompa valores como 'session_id: 20260525_3'
interpretándolos como enteros (202605253).
"""

import sys

try:
    import yaml
except ImportError:
    sys.exit("pyyaml no disponible — instalar con: uv pip install pyyaml")

new_path = sys.argv[1]
old_path = sys.argv[2] if len(sys.argv) > 2 else None

GATEWAY_KEYS = ("gateway_pairings", "gateway_pending_codes")


def extract_top_level_section(text: str, key: str) -> str:
    """Extrae una sección de nivel superior como texto raw (sin parsear YAML).
    Termina cuando encuentra otra clave de nivel superior o EOF.
    """
    lines = text.splitlines(keepends=True)
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.startswith(key + ":"):
            in_section = True
            section_lines.append(line)
        elif in_section:
            # sigue siendo parte de la sección si:
            # - está indentado (sub-clave)
            # - empieza con '-' (item de lista YAML)
            # - es línea vacía o comentario
            if line and not line[0].isspace() and line[0] not in ("-", "#", "\n", "\r"):
                break  # nueva clave top-level → fin de sección
            section_lines.append(line)
    return "".join(section_lines).rstrip()


# ── Cargar el nuevo config (del template renderizado) ──────────────────────
try:
    with open(new_path, encoding="utf-8") as fh:
        new_text = fh.read()
        new_cfg = yaml.safe_load(new_text)
except Exception as e:
    sys.exit(f"error leyendo {new_path}: {e}")

# ── Extraer secciones gateway del config anterior como texto plano ──────────
old_sections: dict[str, str] = {}
if old_path:
    try:
        with open(old_path, encoding="utf-8") as fh:
            old_text = fh.read()
        for key in GATEWAY_KEYS:
            raw = extract_top_level_section(old_text, key)
            if raw and f"{key}: []" not in raw and f"{key}:\n" not in raw.replace(raw.split("\n")[0], "x"):
                # tiene contenido real (no lista vacía y no solo header vacío)
                old_sections[key] = raw
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  advertencia: no se pudo leer config anterior: {e}", file=sys.stderr)

# ── Quitar las secciones gateway del nuevo config (están como []) ──────────
for key in GATEWAY_KEYS:
    new_cfg.pop(key, None)

# ── Volcar nuevo config SIN las secciones gateway ─────────────────────────
with open(new_path, "w", encoding="utf-8") as fh:
    yaml.dump(new_cfg, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # Añadir secciones gateway como texto raw preservado del config anterior
    for key in GATEWAY_KEYS:
        if key in old_sections:
            fh.write("\n" + old_sections[key] + "\n")
        else:
            fh.write(f"\n{key}: []\n")

pairing_count = len(new_cfg.get("gateway_pairings", []))  # solo para display
# Contar en el texto raw si no estaba en new_cfg
if "gateway_pairings" in old_sections:
    pairing_count = old_sections["gateway_pairings"].count("- platform:")
print(f"  gateway_pairings preservados: {pairing_count} entries (texto raw, sin coerción YAML)")
