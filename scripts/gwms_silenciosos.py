"""
Puxa chamados sem interação do GWMS (Grafana/MySQL OTRS) e gera silenciosos.json.
Faz login com GWMS_USER/GWMS_PASS, executa SQL via /grafana/api/ds/query,
transforma a resposta em JSON limpo e sobe no repo via API GitHub.
"""

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

GWMS_URL = "https://gwms2.groundwork.com.br"
DATASOURCE_UID = "PIz1Yx14k"  # MySQL:otrs

# Filas ativas do dashcompleto (mesmas do index.html FILAS_LIST)
FILAS = ("DATASUL", "DBA", "GWMS", "INFRAESTUTURA", "PROTHEUS", "SSG", "SSG-MELHORIAS")

# Estados "fechados" / inativos no OTRS — mesmos filtros do painel original
EXCLUIR_ESTADOS = (2, 3, 5, 7, 9, 11, 16, 17, 18, 19)

# Silêncio mínimo: 1 dia (em segundos)
SILENCIO_MIN_SEC = 86400

SQL = f"""
SELECT
  t.tn                                                       AS ticket,
  t.customer_id                                              AS cliente,
  q.name                                                     AS fila,
  UPPER(ts.name)                                             AS estado,
  tp.name                                                    AS prioridade,
  CONCAT(u.first_name, ' ', u.last_name)                     AS atendente,
  DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')            AS criado,
  DATE_FORMAT(t.change_time, '%Y-%m-%dT%H:%i:%s')            AS modificado,
  (UNIX_TIMESTAMP() - UNIX_TIMESTAMP(t.change_time))         AS silent_sec,
  t.title                                                    AS assunto
FROM ticket t
JOIN queue           q  ON t.queue_id          = q.id
JOIN ticket_state    ts ON t.ticket_state_id   = ts.id
JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
JOIN users           u  ON t.user_id           = u.id
WHERE t.ticket_state_id NOT IN ({','.join(str(s) for s in EXCLUIR_ESTADOS)})
  AND UNIX_TIMESTAMP(t.change_time) <= (UNIX_TIMESTAMP() - {SILENCIO_MIN_SEC})
  AND q.name IN ({','.join(f"'{f}'" for f in FILAS)})
ORDER BY silent_sec DESC
LIMIT 500
"""


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def login(session: requests.Session, user: str, password: str) -> None:
    r = session.post(
        f"{GWMS_URL}/grafana/login",
        json={"user": user, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Login GWMS falhou: HTTP {r.status_code} — {r.text[:200]}")
    log("Login GWMS OK")


def fetch_silenciosos(session: requests.Session) -> list[dict]:
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "mysql", "uid": DATASOURCE_UID},
                "format": "table",
                "rawSql": SQL,
                "rawQuery": True,
            }
        ],
        "from": "now-7d",
        "to": "now",
    }
    r = session.post(
        f"{GWMS_URL}/grafana/api/ds/query",
        json=body,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    frames = data.get("results", {}).get("A", {}).get("frames", [])
    if not frames:
        log("AVISO: Grafana retornou zero frames")
        return []
    frame = frames[0]
    names = [f["name"] for f in frame["schema"]["fields"]]
    values = frame["data"]["values"]
    if not values or not values[0]:
        return []
    rows = []
    for i in range(len(values[0])):
        row = {names[j]: values[j][i] for j in range(len(names))}
        rows.append(row)
    log(f"Query OK — {len(rows)} linhas")
    return rows


def github_upload(path: str, payload_bytes: bytes, message: str) -> None:
    token = os.environ["DEPLOY_TOKEN"]
    repo = os.environ["GH_REPO"]
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    sha_resp = requests.get(api, headers=headers, timeout=15)
    sha = sha_resp.json().get("sha") if sha_resp.status_code == 200 else None
    body = {
        "message": message,
        "content": base64.b64encode(payload_bytes).decode(),
    }
    if sha:
        body["sha"] = sha
    put = requests.put(api, headers=headers, json=body, timeout=60)
    if put.status_code not in (200, 201):
        raise RuntimeError(f"Upload {path} falhou: HTTP {put.status_code} — {put.text[:300]}")
    log(f"Upload {path} OK ({put.status_code})")


def main() -> None:
    user = os.environ.get("GWMS_USER")
    password = os.environ.get("GWMS_PASS")
    if not user or not password:
        print("ERRO: GWMS_USER/GWMS_PASS ausentes", file=sys.stderr)
        sys.exit(1)

    s = requests.Session()
    login(s, user, password)
    rows = fetch_silenciosos(s)

    now = datetime.now(timezone.utc)
    output = {
        "generated_at": int(time.time()),
        "generated_at_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(rows),
        "filas": list(FILAS),
        "silencio_min_sec": SILENCIO_MIN_SEC,
        "rows": rows,
    }
    payload = json.dumps(output, ensure_ascii=False, indent=2).encode("utf-8")
    github_upload("silenciosos.json", payload, f"chore: sync silenciosos ({len(rows)} tickets)")


if __name__ == "__main__":
    main()
