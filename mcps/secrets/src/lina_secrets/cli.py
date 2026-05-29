"""CLI mínima para uso humano: `python -m lina_secrets.cli {get|set|delete|list} ...`"""

from __future__ import annotations

import getpass
import sys

from . import server


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "uso: lina_secrets.cli {get|set|delete|list|backend} <service> [<key>]", file=sys.stderr
        )
        return 2
    cmd = sys.argv[1]
    try:
        if cmd == "get":
            service, key = sys.argv[2], sys.argv[3]
            print(server.secret_get(service, key))
        elif cmd == "set":
            service, key = sys.argv[2], sys.argv[3]
            value = getpass.getpass(f"valor para {service}:{key}: ")
            print(server.secret_set(service, key, value))
        elif cmd == "delete":
            service, key = sys.argv[2], sys.argv[3]
            print(server.secret_delete(service, key))
        elif cmd == "list":
            service = sys.argv[2]
            for k in server.secret_list(service):
                print(k)
        elif cmd == "backend":
            print(server.keyring_backend())
        else:
            print(f"comando desconocido: {cmd}", file=sys.stderr)
            return 2
    except (KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
