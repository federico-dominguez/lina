# Contributing to LINA

Thank you for your interest in contributing to LINA.  
This project is a private personal agent, but the guidelines below apply to all collaborators.

---

## Principles

1. **Don't break LINA.** Every change must leave the agent running correctly.  
   Run `just doctor` before and after your change.
2. **Thinking stays ON.** DeepSeek reasoning (`thinking: enabled`) is a hard constraint.  
   Never remove or disable it. See [Runbook 0001](docs/runbooks/0001-deepseek-v4-reasoning-bug-fix.md).
3. **Secrets never in code.** All credentials go through `lina-secrets` MCP.  
   Never hardcode tokens, passwords, or API keys.
4. **Clean Architecture per MCP.** Each MCP follows the layer order:  
   `domain → application → infrastructure → server.py`.  
   No framework imports in `domain/`. No business logic in `server.py`.
5. **Each tool emits `ToolInvoked`.** When the event bus exists, every tool call is auditable.  
   Until then, log to stderr with the `[lina-<mcp>]` prefix.

---

## Branching strategy

```
main          ← stable; always deployable; protected
  └── feat/<name>    ← new features
  └── fix/<name>     ← bug fixes
  └── runbook/<n>    ← operational fixes applied under a runbook
  └── chore/<name>   ← deps, docs, refactor
```

- Branch names use kebab-case.
- `main` is the only branch deployed to production (the user's desktop).

---

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body — explain WHY, not WHAT]

[optional footer: Closes #N, BREAKING CHANGE: …]
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `perf`.  
Scopes: `mcp/<name>`, `goose`, `config`, `deploy`, `docs`, `ci`.

Examples:
```
feat(mcp/fs-safe): add recursive directory listing tool
fix(goose): backfill reasoning_content to prevent 400 on multi-turn
docs(adr): add ADR 0003 Android control architecture
chore(deps): bump mcp SDK to 1.9.0 in fs-safe
```

---

## Pull requests

- One logical change per PR.
- Fill in the PR template (`.github/pull_request_template.md`).
- Ensure `just doctor` passes.
- Include or update tests for every non-trivial change.
- Add a runbook entry in `docs/runbooks/` for any operational fix.

---

## Adding a new MCP

1. Read [`docs/architecture/0001-clean-architecture.md`](docs/architecture/0001-clean-architecture.md).
2. Scaffold under `mcps/<name>/` mirroring `mcps/fs-safe/`.
3. Register in `config/mcp-registry.yaml`.
4. Add the extension to `config/goose.config.yaml.tmpl`.
5. Update `README.md` MCP Catalog table.
6. Add integration test in `tests/integration/mcps/test_<name>.py`.

---

## Testing

```bash
just test               # unit tests across all MCPs
just test-integration   # integration tests (needs running env)
just doctor             # system health check
```

Tests use `pytest`. Each MCP's `pyproject.toml` includes `pytest` as a dev dependency.

---

## Documentation

- Architecture decisions → `docs/architecture/NNNN-<slug>.md` (ADR format, sequential numbering).
- Operational runbooks → `docs/runbooks/NNNN-<slug>.md` (sequential numbering).
- Session retrospectives → `docs/analysis/YYYY-MM-DD-<slug>.md`.

Never delete existing ADRs or runbooks. Mark them `Estado: superseded by NNNN` instead.

---

## Code review checklist

- [ ] `just doctor` passes
- [ ] No secrets in code or config
- [ ] Thinking not disabled
- [ ] New/modified MCP follows Clean Architecture
- [ ] Tests added or updated
- [ ] Docs updated (README, ADR, or runbook as appropriate)
- [ ] Commit messages follow Conventional Commits
