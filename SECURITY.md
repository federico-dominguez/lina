# Security Policy

## Supported versions

LINA is a personal agent deployed on a single machine. There are no public releases.  
Security fixes are applied to `main` immediately and deployed to production (your desktop).

---

## Threat model

LINA is designed to operate on a trusted personal Linux machine. The primary threats are:

| Threat                              | Mitigation                                                         |
|-------------------------------------|--------------------------------------------------------------------|
| Secrets leaked to the model         | Secrets are read via `lina-secrets` MCP (keyring), never from env/files injected into prompts |
| Arbitrary file writes               | `lina-fs-safe` enforces an allowlist (`LINA_FS_ALLOWLIST`)         |
| Unaudited shell execution           | `lina-shell-policy` maintains a command allowlist + timeout        |
| Sudo escalation                     | `LINA_SHELL_ALLOW_SUDO=0` by default; enabled only explicitly      |
| API keys in source control          | `.gitignore` excludes `.env`, `secrets.env` and `*.token`          |
| Prompt injection via tool output    | Goose's system prompt instructs the model to distrust untrusted data; watch for suspicious `<SYSTEM>` / `[INST]` patterns in tool results |
| Telegram bot token exposure         | Token stored in keyring + secrets.env (chmod 600), never logged    |

---

## Reporting a vulnerability

This is a private repository. If you discover a security issue:

1. **Do not open a public issue.**
2. Open a [GitHub Security Advisory](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/creating-a-repository-security-advisory) on this repository.
3. Include: description, reproduction steps, impact, and suggested fix.

Expected response: acknowledgment within 48 hours.

---

## Security checklist for contributors

- [ ] No secrets, tokens, or passwords in code, config, or commit messages
- [ ] New shell commands added to `lina-shell-policy` allowlist (not bypass)
- [ ] New file operations go through `lina-fs-safe` (not raw `open()`)
- [ ] New MCP tools do not expose raw `eval()` or `exec()` without sandboxing
- [ ] Dependencies pinned in `uv.lock` — run `uv audit` before merging
- [ ] Any webhook or HTTP endpoint requires authentication
