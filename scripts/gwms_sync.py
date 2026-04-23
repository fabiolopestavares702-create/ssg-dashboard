"""
Sync GWMS data → JSONs no repo.

Puxa 4 datasets do MySQL OTRS via Grafana API:
  - silenciosos.json  : tickets sem interação há ≥ 1 dia
  - triagem.json      : tickets em triagem agora (state=open sem movimentação)
  - reaberturas.json  : tickets reabertos (já passaram por fechamento e voltaram)
  - utilizacao.json   : carga atual por atendente (tickets ativos, em atendimento)

Uso: chamado pelo workflow .github/workflows/gwms-sync.yml
"""

import base64
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import requests

GWMS_URL = "https://gwms2.groundwork.com.br"
DATASOURCE_UID = "PIz1Yx14k"  # MySQL:otrs

# Filas ativas do dashcompleto (mesmas do index.html FILAS_LIST)
FILAS = ("DATASUL", "DBA", "GWMS", "INFRAESTUTURA", "PROTHEUS", "SSG", "SSG-MELHORIAS")
FILAS_SQL = ",".join(f"'{f}'" for f in FILAS)

# Estados fechados (closed successful, unsuccessful, removed, merged,
# fechado sem êxito auto, resolvido, cancelado)
ESTADOS_FECHADOS = (2, 3, 5, 9, 17, 18, 19)
# Estado "em atendimento" é id=14
# Estado "open" (triagem inicial) é id=4
# History type StateUpdate=27, OwnerUpdate=23, Move=16

# Os mesmos estados que o painel silenciosos exclui
SILEN_EXCLUIR = (2, 3, 5, 7, 9, 11, 16, 17, 18, 19)
SILEN_EXCLUIR_SQL = ",".join(str(s) for s in SILEN_EXCLUIR)

# Estados considerados ativos/abertos (excluímos fechados e estados que "aguardam")
ESTADOS_ATIVOS = (4, 11, 12, 13, 14, 15, 20)
ESTADOS_ATIVOS_SQL = ",".join(str(s) for s in ESTADOS_ATIVOS)


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


def query_mysql(session: requests.Session, sql: str) -> list[dict]:
    body = {
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "mysql", "uid": DATASOURCE_UID},
                "format": "table",
                "rawSql": sql,
                "rawQuery": True,
            }
        ],
        "from": "now-7d",
        "to": "now",
    }
    r = session.post(f"{GWMS_URL}/grafana/api/ds/query", json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    frames = data.get("results", {}).get("A", {}).get("frames", [])
    if not frames:
        return []
    frame = frames[0]
    names = [f["name"] for f in frame["schema"]["fields"]]
    values = frame["data"]["values"]
    if not values or not values[0]:
        return []
    rows = []
    for i in range(len(values[0])):
        rows.append({names[j]: values[j][i] for j in range(len(names))})
    return rows


# ─── QUERIES ────────────────────────────────────────────────────────────────

def q_silenciosos(session: requests.Session) -> list[dict]:
    sql = f"""
    SELECT
      t.tn                                                     AS ticket,
      t.customer_id                                            AS cliente,
      q.name                                                   AS fila,
      UPPER(ts.name)                                           AS estado,
      tp.name                                                  AS prioridade,
      CONCAT(u.first_name, ' ', u.last_name)                   AS atendente,
      DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')          AS criado,
      DATE_FORMAT(t.change_time, '%Y-%m-%dT%H:%i:%s')          AS modificado,
      (UNIX_TIMESTAMP() - UNIX_TIMESTAMP(t.change_time))       AS silent_sec,
      t.title                                                  AS assunto
    FROM ticket t
    JOIN queue           q  ON t.queue_id           = q.id
    JOIN ticket_state    ts ON t.ticket_state_id    = ts.id
    JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
    JOIN users           u  ON t.user_id            = u.id
    WHERE t.ticket_state_id NOT IN ({SILEN_EXCLUIR_SQL})
      AND UNIX_TIMESTAMP(t.change_time) <= (UNIX_TIMESTAMP() - 86400)
      AND q.name IN ({FILAS_SQL})
    ORDER BY silent_sec DESC
    LIMIT 500
    """
    return query_mysql(session, sql)


def q_triagem(session: requests.Session) -> list[dict]:
    """Tickets atualmente em 'open' (state=4) sem nenhuma movimentação após criação.
    Movimentação = StateUpdate (27), OwnerUpdate (23), Move (16)."""
    sql = f"""
    SELECT
      t.tn                                                     AS ticket,
      t.customer_id                                            AS cliente,
      q.name                                                   AS fila,
      UPPER(ts.name)                                           AS estado,
      tp.name                                                  AS prioridade,
      CONCAT(u.first_name, ' ', u.last_name)                   AS atendente,
      DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')          AS criado,
      TIMESTAMPDIFF(MINUTE, t.create_time, NOW())              AS triagem_min,
      t.title                                                  AS assunto
    FROM ticket t
    JOIN queue           q  ON t.queue_id           = q.id
    JOIN ticket_state    ts ON t.ticket_state_id    = ts.id
    JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
    JOIN users           u  ON t.user_id            = u.id
    WHERE t.ticket_state_id = 4
      AND q.name IN ({FILAS_SQL})
      AND NOT EXISTS(
        SELECT 1 FROM ticket_history th
        WHERE th.ticket_id = t.id
          AND th.history_type_id IN (27, 23, 16)
          AND th.create_time > DATE_ADD(t.create_time, INTERVAL 2 SECOND)
      )
    ORDER BY triagem_min DESC
    LIMIT 200
    """
    return query_mysql(session, sql)


def q_reaberturas(session: requests.Session) -> list[dict]:
    """Tickets criados nos últimos 90 dias que já passaram por algum estado
    fechado e voltaram pra um estado ativo."""
    sql = f"""
    SELECT
      t.tn                                                     AS ticket,
      t.customer_id                                            AS cliente,
      q.name                                                   AS fila,
      UPPER(ts.name)                                           AS estado_atual,
      tp.name                                                  AS prioridade,
      CONCAT(u.first_name, ' ', u.last_name)                   AS atendente,
      DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')          AS criado,
      DATE_FORMAT(t.change_time, '%Y-%m-%dT%H:%i:%s')          AS modificado,
      COUNT(DISTINCT CASE WHEN th.state_id IN ({','.join(str(s) for s in ESTADOS_FECHADOS)}) THEN th.id END) AS vezes_fechado,
      t.title                                                  AS assunto
    FROM ticket t
    JOIN queue           q  ON t.queue_id           = q.id
    JOIN ticket_state    ts ON t.ticket_state_id    = ts.id
    JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
    JOIN users           u  ON t.user_id            = u.id
    LEFT JOIN ticket_history th ON th.ticket_id = t.id AND th.history_type_id = 27
    WHERE t.ticket_state_id NOT IN ({','.join(str(s) for s in ESTADOS_FECHADOS)})
      AND q.name IN ({FILAS_SQL})
      AND t.create_time >= DATE_SUB(NOW(), INTERVAL 90 DAY)
    GROUP BY t.id
    HAVING vezes_fechado >= 1
    ORDER BY vezes_fechado DESC, t.change_time DESC
    LIMIT 300
    """
    return query_mysql(session, sql)


def q_historico_completo(session: requests.Session) -> list[dict]:
    """Equivalente completo ao XLSX do dashcompleto — todos os campos que o
    dashboard consome, direto do MySQL OTRS. Substitui (no futuro) dados.xlsx.

    Traz tickets criados nos últimos 4 meses (2000-3000 linhas típicas).
    Campos computados server-side: resp_min_raw, fechado (via ticket_history),
    primeira_resposta (via article). Horas úteis e sol_min são calculados
    em Python no pós-processamento pra não estourar timeout do SQL."""
    sql = f"""
    SELECT
      t.tn                                                      AS num,
      t.title                                                   AS assunto,
      DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')           AS criado,
      DATE_FORMAT(t.change_time, '%Y-%m-%dT%H:%i:%s')           AS modificado,
      q.name                                                    AS fila,
      ts.name                                                   AS estado,
      tp.name                                                   AS prioridade,
      CONCAT(COALESCE(u.first_name,''), ' ', COALESCE(u.last_name,'')) AS atendente,
      COALESCE(u.first_name, '')                                AS primeiro_nome,
      COALESCE(u.last_name, '')                                 AS ultimo_nome,
      t.customer_id                                             AS cli_id,
      t.customer_user_id                                        AS cli_user,
      COALESCE(s.name, '')                                      AS servico,
      (SELECT DATE_FORMAT(MAX(th.create_time), '%Y-%m-%dT%H:%i:%s')
       FROM ticket_history th
       WHERE th.ticket_id = t.id
         AND th.state_id IN (2, 3, 5, 9, 17, 18, 19))            AS fechado,
      (SELECT TIMESTAMPDIFF(MINUTE, t.create_time, MIN(a.create_time))
       FROM article a
       WHERE a.ticket_id = t.id
         AND a.article_sender_type_id = 1
         AND a.is_visible_for_customer = 1
         AND a.create_time > t.create_time)                      AS resp_min_raw
    FROM ticket t
    JOIN queue           q  ON t.queue_id           = q.id
    JOIN ticket_state    ts ON t.ticket_state_id    = ts.id
    JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
    LEFT JOIN users      u  ON t.user_id            = u.id
    LEFT JOIN service    s  ON t.service_id         = s.id
    WHERE q.name IN ({FILAS_SQL})
      AND t.create_time >= DATE_SUB(NOW(), INTERVAL 4 MONTH)
    ORDER BY t.create_time DESC
    LIMIT 5000
    """
    rows = query_mysql(session, sql)

    # ─── Busca transições de estado de todos os tickets em 1 query ────
    # Usado pra calcular sol_min_bh com exclusão de tempo "aguardando cliente",
    # replicando o cálculo do Znuny.
    state_sql = f"""
    SELECT
      t.tn                                          AS tn,
      DATE_FORMAT(th.create_time, '%Y-%m-%dT%H:%i:%s') AS dt,
      th.state_id                                   AS state_id
    FROM ticket t
    JOIN queue q ON t.queue_id = q.id
    JOIN ticket_history th ON th.ticket_id = t.id
    WHERE q.name IN ({FILAS_SQL})
      AND t.create_time >= DATE_SUB(NOW(), INTERVAL 4 MONTH)
      AND th.history_type_id = 27
    ORDER BY t.tn, th.create_time
    """
    transitions_raw = query_mysql(session, state_sql)
    transitions_by_tn: dict = {}
    for tr in transitions_raw:
        tn = str(tr.get("tn"))
        if tn not in transitions_by_tn:
            transitions_by_tn[tn] = []
        transitions_by_tn[tn].append(tr)
    log(f"  transições de estado: {len(transitions_raw)} linhas ({len(transitions_by_tn)} tickets)")

    # Pós-processa: horas úteis em minutos (sol_min e resp_min_bh)
    # Business hours: Seg–Sex 8h–12h + 13h–18h (pausa almoço excluída)
    WAIT_STATE_IDS = {6, 7, 8, 11, 12, 13, 16, 20}  # aguardando cliente/interno/externo + pending + melhoria + gmud
    CLOSED_STATE_IDS = {2, 3, 5, 9, 17, 18, 19}

    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None

    def _business_minutes(start, end):
        """Horas úteis Seg-Sex 8-12 + 13-18 (9h/dia)."""
        if not start or not end or end <= start:
            return 0 if end == start else None
        BLOCKS = [(8, 12), (13, 18)]
        total = 0
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        max_days = 400
        while cur < end and max_days > 0:
            max_days -= 1
            if cur.weekday() < 5:
                for bs, be in BLOCKS:
                    b_start = cur.replace(hour=bs)
                    b_end = cur.replace(hour=be)
                    p_start = start if start > b_start else b_start
                    p_end = end if end < b_end else b_end
                    if p_end > p_start:
                        total += int((p_end - p_start).total_seconds() // 60)
            cur = cur.replace(hour=0) + timedelta(days=1)
        return total

    def _compute_sol_min_bh_active(ticket_start, transitions, ticket_end):
        """Calcula minutos úteis APENAS nos estados não-wait e não-closed.
        transitions = lista ordenada de dicts {dt, state_id}.
        ticket_end = quando foi primeiro fechado (máximo do intervalo)."""
        if not ticket_start or not transitions:
            return None
        # Segmentos [start, end, state_id]. O primeiro segmento antes de qualquer
        # StateUpdate usa estado "inicial" (assumido não-wait, tipicamente new/open)
        segments = []
        prev_time = ticket_start
        prev_state = None  # estado inicial: não-wait
        for tr in transitions:
            dt = _parse_dt(tr.get("dt"))
            sid = tr.get("state_id")
            if dt is None:
                continue
            if dt <= prev_time:  # pula transições fora de ordem
                prev_time = dt
                prev_state = sid
                continue
            segments.append((prev_time, dt, prev_state))
            prev_time = dt
            prev_state = sid
            if sid in CLOSED_STATE_IDS:
                break
        # Se não entrou em closed e ainda temos ticket_end, adiciona segmento final
        if segments and prev_state not in CLOSED_STATE_IDS and ticket_end and prev_time < ticket_end:
            segments.append((prev_time, ticket_end, prev_state))
        total = 0
        for s, e, st in segments:
            if st in CLOSED_STATE_IDS:
                break
            if st in WAIT_STATE_IDS:
                continue  # tempo parado, não conta
            bm = _business_minutes(s, e)
            if bm:
                total += bm
        return total

    for r in rows:
        cr = _parse_dt(r.get("criado"))
        fe = _parse_dt(r.get("fechado"))
        resp_raw = r.get("resp_min_raw")
        tn = str(r.get("num"))
        trans = transitions_by_tn.get(tn, [])

        # sol_min_raw (wall-clock criado → fechado)
        if cr and fe and fe > cr:
            r["sol_min_raw"] = int((fe - cr).total_seconds() // 60)
            # sol_min_bh com exclusão de estados wait (replicando Znuny)
            r["sol_min_bh"] = _compute_sol_min_bh_active(cr, trans, fe)
        else:
            r["sol_min_raw"] = None
            r["sol_min_bh"] = None

        # resp_min_bh — primeira resposta em horas úteis
        if resp_raw is not None and cr:
            fr = cr + timedelta(minutes=int(resp_raw))
            r["resp_min_bh"] = _business_minutes(cr, fr)
        else:
            r["resp_min_bh"] = None
    return rows


def q_tickets_ativos(session: requests.Session) -> list[dict]:
    """Snapshot atual de todos os tickets ativos (não fechados).
    Usado para o modal de detalhe de ticket (Alertas Críticos, Silenciosos, etc.)
    — garante que o card mostre estado atual e não o XLSX possivelmente defasado."""
    sql = f"""
    SELECT
      t.tn                                                       AS ticket,
      t.customer_id                                              AS cliente,
      q.name                                                     AS fila,
      UPPER(ts.name)                                             AS estado,
      tp.name                                                    AS prioridade,
      CONCAT(u.first_name, ' ', u.last_name)                     AS atendente,
      DATE_FORMAT(t.create_time, '%Y-%m-%dT%H:%i:%s')            AS criado,
      DATE_FORMAT(t.change_time, '%Y-%m-%dT%H:%i:%s')            AS modificado,
      TIMESTAMPDIFF(DAY, t.create_time, NOW())                   AS idade_dias,
      t.title                                                    AS assunto
    FROM ticket t
    JOIN queue           q  ON t.queue_id           = q.id
    JOIN ticket_state    ts ON t.ticket_state_id    = ts.id
    JOIN ticket_priority tp ON t.ticket_priority_id = tp.id
    JOIN users           u  ON t.user_id            = u.id
    WHERE t.ticket_state_id NOT IN ({','.join(str(s) for s in ESTADOS_FECHADOS)})
      AND q.name IN ({FILAS_SQL})
    ORDER BY t.change_time DESC
    LIMIT 3000
    """
    return query_mysql(session, sql)


def q_utilizacao(session: requests.Session) -> list[dict]:
    """Carga atual por atendente: count de ativos e em_atendimento.
    Simplificação: não computa % de utilização histórica (exigiria time-series),
    apenas snapshot atual."""
    sql = f"""
    SELECT
      CONCAT(u.first_name, ' ', u.last_name)                                         AS atendente,
      COUNT(*)                                                                        AS ativos_total,
      SUM(CASE WHEN t.ticket_state_id = 14 THEN 1 ELSE 0 END)                         AS em_atendimento,
      SUM(CASE WHEN t.ticket_state_id IN (11, 12, 13, 15) THEN 1 ELSE 0 END)          AS aguardando,
      SUM(CASE WHEN t.ticket_state_id = 4 THEN 1 ELSE 0 END)                          AS abertos,
      AVG(CASE WHEN t.ticket_state_id = 14 THEN TIMESTAMPDIFF(MINUTE, t.change_time, NOW()) END) AS em_atend_tempo_med_min
    FROM ticket t
    JOIN users u ON t.user_id = u.id
    JOIN queue q ON t.queue_id = q.id
    WHERE q.name IN ({FILAS_SQL})
      AND t.ticket_state_id IN ({ESTADOS_ATIVOS_SQL})
    GROUP BY u.id
    ORDER BY ativos_total DESC
    LIMIT 50
    """
    return query_mysql(session, sql)


# ─── UPLOAD ──────────────────────────────────────────────────────────────────

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


def make_envelope(rows: list, extra=None) -> dict:
    now = datetime.now(timezone.utc)
    env = {
        "generated_at": int(time.time()),
        "generated_at_iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(rows),
        "filas": list(FILAS),
        "rows": rows,
    }
    if extra:
        env.update(extra)
    return env


def main() -> None:
    user = os.environ.get("GWMS_USER")
    password = os.environ.get("GWMS_PASS")
    if not user or not password:
        print("ERRO: GWMS_USER/GWMS_PASS ausentes", file=sys.stderr)
        sys.exit(1)

    s = requests.Session()
    login(s, user, password)

    datasets = [
        ("silenciosos.json",       q_silenciosos,       {"silencio_min_sec": 86400}),
        ("triagem.json",           q_triagem,           None),
        ("reaberturas.json",       q_reaberturas,       {"janela_dias": 90}),
        ("utilizacao.json",        q_utilizacao,        None),
        ("tickets_ativos.json",    q_tickets_ativos,    None),
        ("historico_completo.json", q_historico_completo, {"janela_meses": 4, "obs": "shadow mode — substitui dados.xlsx na fase 4"}),
    ]

    collected = {}
    failures = []
    for fname, qfn, extra in datasets:
        try:
            rows = qfn(s)
            log(f"{fname}: {len(rows)} linhas")
            collected[fname] = rows
            payload = json.dumps(make_envelope(rows, extra), ensure_ascii=False, indent=2).encode("utf-8")
            github_upload(fname, payload, f"chore: sync {fname} ({len(rows)} linhas)")
        except Exception as e:
            log(f"ERRO em {fname}: {e}")
            failures.append(fname)

    # ── Insights (regras determinísticas sobre os dados em memória) ──
    try:
        ins = generate_insights(collected)
        log(f"gwms-insights.json: {len(ins['insights'])} insights (crit={ins['counts']['crit']}, warn={ins['counts']['warn']})")
        payload = json.dumps(ins, ensure_ascii=False, indent=2).encode("utf-8")
        github_upload("gwms-insights.json", payload, f"chore: sync gwms-insights ({len(ins['insights'])} insights)")
    except Exception as e:
        log(f"ERRO em gwms-insights.json: {e}")
        failures.append("gwms-insights.json")

    if failures:
        print(f"FALHAS: {failures}", file=sys.stderr)
        sys.exit(1)


# ─── INSIGHTS (regras determinísticas) ──────────────────────────────────────

def generate_insights(data: dict) -> dict:
    """Gera insights a partir dos 4 datasets em memória.
    Cada regra é uma função pequena que retorna 0 ou mais insight-dicts."""
    insights = []

    silenciosos = data.get("silenciosos.json", [])
    triagem     = data.get("triagem.json", [])
    reaberturas = data.get("reaberturas.json", [])
    utilizacao  = data.get("utilizacao.json", [])

    insights.extend(_rule_reaberturas_recorrentes(reaberturas))
    insights.extend(_rule_tickets_abandonados(triagem, silenciosos))
    insights.extend(_rule_sobrecarga_atendente(utilizacao))
    insights.extend(_rule_fila_concentrada(silenciosos))

    counts = {"crit": 0, "warn": 0, "ok": 0}
    for i in insights:
        sev = i.get("severity", "ok")
        counts[sev] = counts.get(sev, 0) + 1

    return {
        "generated_at": int(time.time()),
        "generated_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": counts,
        "insights": insights,
    }


def _rule_reaberturas_recorrentes(reaberturas: list) -> list:
    """Detecta padrão de cliente com múltiplos tickets reabertos 2x+."""
    multi = [r for r in reaberturas if (r.get("vezes_fechado") or 0) >= 2]
    if not multi:
        return []
    # Agrupa por cliente
    by_cli = defaultdict(list)
    for r in multi:
        by_cli[r.get("cliente") or "—"].append(r)
    out = []
    for cli, rows in by_cli.items():
        if len(rows) < 2:
            continue  # precisa 2+ tickets recorrentes pra virar padrão
        filas = sorted(set(r.get("fila", "") for r in rows))
        atendentes = sorted(set((r.get("atendente") or "").split(" ")[0] for r in rows if r.get("atendente")))
        out.append({
            "id": f"reaberturas_recorrentes_{cli}",
            "severity": "warn",
            "category": "qualidade",
            "title": f"Padrão de reabertura recorrente em {cli}",
            "summary": (
                f"{len(rows)} tickets reabertos 2× ou mais nos últimos 90d em {cli}, "
                f"concentrados em {', '.join(filas)}. "
                "Indica fechamento prematuro ou escopo mal definido."
            ),
            "evidence": [
                {"ticket": r["ticket"], "fila": r.get("fila"), "atendente": r.get("atendente"),
                 "vezes_fechado": r.get("vezes_fechado"), "estado_atual": r.get("estado_atual")}
                for r in rows[:10]
            ],
            "recommendation": (
                f"Revisar critério de encerramento com {', '.join(atendentes[:3])}. "
                "Audit de 5 tickets por atendente pra entender se fechamento está sendo prematuro."
            ),
            "impact_estimate": "médio",
        })
    return out


def _rule_tickets_abandonados(triagem: list, silenciosos: list) -> list:
    """Tickets em triagem > 30 dias OU silenciosos > 60 dias."""
    ABAND_TRIAGEM_MIN = 30 * 24 * 60   # 30 dias em minutos
    ABAND_SILENT_SEC  = 60 * 24 * 3600  # 60 dias em segundos

    abandonados_tri = [r for r in triagem if (r.get("triagem_min") or 0) >= ABAND_TRIAGEM_MIN]
    abandonados_sil = [r for r in silenciosos if (r.get("silent_sec") or 0) >= ABAND_SILENT_SEC]

    out = []
    if abandonados_tri:
        sev = "crit" if len(abandonados_tri) >= 1 else "warn"
        out.append({
            "id": "tickets_abandonados_triagem",
            "severity": sev,
            "category": "sla",
            "title": f"{len(abandonados_tri)} ticket(s) em triagem abandonada (>30 dias)",
            "summary": (
                f"{len(abandonados_tri)} ticket(s) em estado OPEN sem qualquer movimentação há "
                f"mais de 30 dias. Risco de violação contínua de SLA e insatisfação crônica do cliente."
            ),
            "evidence": [
                {"ticket": r["ticket"], "cliente": r.get("cliente"), "fila": r.get("fila"),
                 "atendente": r.get("atendente"), "dias": round((r.get("triagem_min") or 0) / 1440, 1),
                 "criado": r.get("criado")}
                for r in sorted(abandonados_tri, key=lambda x: -(x.get("triagem_min") or 0))[:10]
            ],
            "recommendation": "Reatribuir a outro atendente ou fechar com justificativa formal. Validar se o cliente ainda precisa.",
            "impact_estimate": "alto",
        })
    if abandonados_sil:
        out.append({
            "id": "tickets_silenciosos_longos",
            "severity": "warn",
            "category": "sla",
            "title": f"{len(abandonados_sil)} ticket(s) silenciosos há mais de 60 dias",
            "summary": (
                f"{len(abandonados_sil)} tickets abertos sem nenhuma interação há mais de 60 dias. "
                "Pode indicar tickets abandonados mesmo tendo sido tocados no passado."
            ),
            "evidence": [
                {"ticket": r["ticket"], "cliente": r.get("cliente"), "fila": r.get("fila"),
                 "atendente": r.get("atendente"), "dias_silencio": round((r.get("silent_sec") or 0) / 86400, 1),
                 "estado": r.get("estado")}
                for r in sorted(abandonados_sil, key=lambda x: -(x.get("silent_sec") or 0))[:10]
            ],
            "recommendation": "Revisar em bloco: cada atendente confirma se o ticket está ativo ou deve ser fechado/escalado.",
            "impact_estimate": "médio",
        })
    return out


def _rule_sobrecarga_atendente(utilizacao: list) -> list:
    """Atendentes com carga real alta — só considera tickets na mão do atendente
    (em_atendimento + abertos). Tickets aguardando cliente/externo/interno NÃO
    contam porque estão esperando terceiros, não consomem capacidade do atendente."""
    if not utilizacao:
        return []
    THRESHOLD_WARN = 5
    THRESHOLD_CRIT = 8

    def carga_real(u: dict) -> int:
        return int((u.get("em_atendimento") or 0) + (u.get("abertos") or 0))

    sobrecarregados = [u for u in utilizacao if carga_real(u) >= THRESHOLD_WARN]
    if not sobrecarregados:
        return []
    out = []
    for u in sobrecarregados:
        cr = carga_real(u)
        out.append({
            "id": f"sobrecarga_{(u.get('atendente') or '').replace(' ','_')}",
            "severity": "crit" if cr >= THRESHOLD_CRIT else "warn",
            "category": "capacidade",
            "title": f"{u.get('atendente')} com carga real de {cr} tickets",
            "summary": (
                f"{u.get('atendente')} tem {cr} tickets ativos sob sua responsabilidade: "
                f"{u.get('em_atendimento', 0)} em atendimento + {u.get('abertos', 0)} abertos "
                f"(além de {u.get('aguardando', 0)} aguardando terceiros, que não consomem capacidade). "
                "Avaliar se há gargalo ou se é volume esperado para o atendente."
            ),
            "evidence": [{
                "atendente": u.get("atendente"),
                "carga_real": cr,
                "em_atendimento": u.get("em_atendimento"),
                "abertos": u.get("abertos"),
                "aguardando_terceiros": u.get("aguardando"),
                "ativos_total": u.get("ativos_total"),
            }],
            "recommendation": (
                "Revisar prioridades com o atendente. Se mantido o volume, considerar redistribuição — "
                "mas atribuição depende de skill/fila (não sugiro automaticamente para evitar erro de matching)."
            ),
            "impact_estimate": "médio",
        })
    return out


INTERNAL_DOMAINS = ("groundwork.com.br", "groundwork.com", "ssg.com.br")


def _is_internal_customer(cli: str) -> bool:
    """Customer_id do OTRS às vezes é email interno de quem abriu o ticket —
    não é cliente real. Filtra esses."""
    if not cli:
        return False
    low = cli.lower()
    return any(d in low for d in INTERNAL_DOMAINS)


def _rule_fila_concentrada(silenciosos: list) -> list:
    """Fila com silenciosos concentrados em um único cliente real (>60%).
    Ignora tickets cujo customer_id é email interno (não é cliente real)."""
    if not silenciosos:
        return []
    # Filtra tickets internos globalmente antes de agregar
    externos = [r for r in silenciosos if not _is_internal_customer(r.get("cliente", ""))]
    by_fila = defaultdict(list)
    for r in externos:
        by_fila[r.get("fila") or "—"].append(r)
    out = []
    for fila, rows in by_fila.items():
        if len(rows) < 5:
            continue
        by_cli = Counter(r.get("cliente", "—") for r in rows)
        top_cli, top_n = by_cli.most_common(1)[0]
        pct = top_n / len(rows)
        if pct >= 0.6:
            out.append({
                "id": f"fila_concentrada_{fila}_{top_cli}",
                "severity": "warn",
                "category": "distribuicao",
                "title": f"Fila {fila} concentrada em {top_cli}",
                "summary": (
                    f"{top_n} de {len(rows)} tickets silenciosos na fila {fila} ({int(pct*100)}%) "
                    f"são do cliente {top_cli}. Concentração alta pode indicar SLA subdimensionado ou problema recorrente."
                ),
                "evidence": [
                    {"fila": fila, "cliente_top": top_cli, "n_top": top_n, "n_total": len(rows),
                     "pct_top": round(pct, 2),
                     "distribuicao": dict(by_cli.most_common(5))}
                ],
                "recommendation": (
                    f"Revisar contrato/SLA de {top_cli} na fila {fila}. Avaliar alocação de recurso "
                    "específico ou negociar volume diferenciado."
                ),
                "impact_estimate": "médio",
            })
    return out


if __name__ == "__main__":
    main()
