import requests, os, json, sys, glob, base64, time
from datetime import datetime
from pathlib import Path

# ── credenciais via ~/.ssg_env ────────────────────────────────
def _load_env():
    p = os.path.expanduser("~/.ssg_env")
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO         = os.environ["GITHUB_REPO"]
PASTA        = os.environ.get("WORK_DIR", str(Path.home() / "Desktop" / "Arquivo OTRS"))
HTML_PATH    = os.path.join(PASTA, "index.html")
LOG_PATH     = os.path.join(PASTA, "ssg_log.txt")
API          = "https://api.github.com"

def log(m):
    msg = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(msg)
    try:
        with open(LOG_PATH, "a") as f: f.write(msg + "\n")
    except: pass

def get_sha(path):
    r = requests.get(f"{API}/repos/{REPO}/contents/{path}",
        headers={"Authorization": f"token {GITHUB_TOKEN}"}, timeout=10)
    return r.json().get("sha") if r.status_code == 200 else None

def upload(path, content, msg):
    sha = get_sha(path)
    payload = {"message": msg, "content": base64.b64encode(content).decode()}
    if sha: payload["sha"] = sha
    r = requests.put(f"{API}/repos/{REPO}/contents/{path}",
        headers={"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(payload), timeout=60)
    if r.status_code not in [200, 201]:
        # Melhoria #3: loga HTTP status + corpo para diagnóstico imediato
        try:
            detail = r.json().get("message", r.text[:200])
        except Exception:
            detail = r.text[:200]
        log(f"  upload falhou — HTTP {r.status_code}: {detail}")
        return False
    return True

def ultimo_xlsx():
    arquivos = glob.glob(os.path.join(PASTA, "ticket_search_*.xlsx"))
    return sorted(arquivos)[-1] if arquivos else None

def deploy():
    log("Iniciando deploy GitHub Pages...")

    # index.html
    if not os.path.exists(HTML_PATH):
        log("ERRO: index.html nao encontrado"); sys.exit(1)
    with open(HTML_PATH, "rb") as f: html = f.read()
    log(f"HTML: {len(html):,} bytes")
    if not upload("index.html", html, "deploy automatico"):
        log("ERRO ao enviar HTML"); sys.exit(1)
    log("HTML enviado.")

    # dados.xlsx
    xlsx_path = ultimo_xlsx()
    if not xlsx_path:
        log("ERRO: nenhum ticket_search_*.xlsx encontrado"); sys.exit(1)
    idade_min = (time.time() - os.path.getmtime(xlsx_path)) / 60
    if idade_min > 20:
        log(f"AVISO: xlsx tem {int(idade_min)}min — extração pode ter falhado silenciosamente")
    with open(xlsx_path, "rb") as f: xlsx = f.read()
    if not upload("dados.xlsx", xlsx, "dados atualizados"):
        log("ERRO ao enviar dados.xlsx"); sys.exit(1)
    log(f"XLS enviado: {os.path.basename(xlsx_path)} ({int(idade_min)}min atrás)")

    # aios-insights.json
    insights_path = os.path.join(PASTA, "aios-insights.json")
    if os.path.exists(insights_path):
        with open(insights_path, "rb") as f: insights = f.read()
        if upload("aios-insights.json", insights, "insights IA"):
            log("aios-insights.json enviado.")
        else:
            log("AVISO: falha ao enviar aios-insights.json")

    # meta.json
    meta = json.dumps({
        "deploy_ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "xlsx_file": os.path.basename(xlsx_path) if xlsx_path else ""
    }).encode()
    if upload("meta.json", meta, "meta"):
        log("meta.json enviado.")

    log(f"Deploy finalizado: https://{REPO.split('/')[0]}.github.io/ssg-dashboard/")

if __name__ == "__main__":
    deploy()
