# AGENTS.cline — Reglas profesionales de desarrollo

## Branch Naming
```
fix/issue-<N>-<slug>       # Bugs
feat/issue-<N>-<slug>      # Features  
refactor/issue-<N>-<slug>  # Refactors
docs/issue-<N>-<slug>      # Documentation
```

## Commits (Conventional)
```
feat:     # Nueva funcionalidad
fix:      # Corrección de bug
refactor: # Cambio de estructura sin cambio funcional
test:     # Agregar o corregir tests
docs:     # Documentación
chore:    # Mantenimiento, CI, config
```

## Workflow obligatorio
1. `git checkout main && git pull`
2. `git checkout -b <branch-segun-tipo>`
3. Implementar código (SOLO lo necesario, no más)
4. Tests unitarios: `python3 -m pytest tests/ -x --tb=short`
5. Si fallan → corregir → repetir paso 4
6. `git add -A && git commit -m "<tipo>: descripción corta y precisa"`
7. `git push origin HEAD`
8. Crear PR: `gh pr create --fill --draft`
9. Esperar CI verde (gh run watch)
10. Si CI rojo → corregir → push de nuevo
11. `gh pr ready` (marcar como listo para review)

## Prohibido
- ❌ Pushear código sin tests
- ❌ Commits gigantes (>15 archivos)
- ❌ Código muerto o comentado
- ❌ Variables mal nombradas (a, b, x, tmp)
- ❌ Funciones de más de 50 líneas

## Tests
- Un test por cada función/método nuevo
- Edge cases: vacío, nulo, error, límite
- Los tests DEBEN pasar antes de cualquier push
- `python3 -m pytest tests/ -x --tb=short`

## Code style
- Python: PEP 8, type hints, docstrings
- Nombres descriptivos en inglés o español
- logging en vez de print
- Errores: excepciones específicas, no except Exception genérico
