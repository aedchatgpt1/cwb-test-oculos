#!/usr/bin/env python3
"""Ponte CWB: leva o que você fala nos óculos até o Claude Code do seu Mac
(sua assinatura, sem API paga) e devolve a resposta para a lente.

Uso:  python3 ponte.py          (escuta em 127.0.0.1:8787)
Depois, em outro Terminal:  tailscale funnel 8787

Segurança:
- Só aceita pedidos com a chave guardada em ~/.cwb-ponte/chave (criada na 1a vez).
- Só aceita a página do app (github.io do André) via CORS.
- O Claude roda SEM ferramentas: só conversa. Não manda mensagem, não mexe
  em arquivo, CRM nem compra nada. Ações continuam passando pela pinça.
"""
import hmac, json, os, secrets, shutil, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = int(os.environ.get("PONTE_PORTA", "8787"))
ORIGEM = "https://aedchatgpt1.github.io"
BASE = Path.home() / ".cwb-ponte"
TRABALHO = BASE / "trabalho"
BASE.mkdir(exist_ok=True); TRABALHO.mkdir(exist_ok=True)
arq_chave = BASE / "chave"
if not arq_chave.exists():
    arq_chave.write_text(secrets.token_urlsafe(24)); arq_chave.chmod(0o600)
CHAVE = arq_chave.read_text().strip()

TELAS = ["home","briefing","inbox","approve","clients","client","visita","jobs",
         "gmail","agenda","drive","whatsapp","homedepot","amazon","thumbtack",
         "social","conexoes","more","claude"]

INSTRUCOES = (
    "Você é o Claude do André Lima (CWB General Contracting), respondendo na lente "
    "do Meta Ray-Ban Display. Ele fala por voz, muitas vezes trabalhando ou dirigindo. "
    "Responda em português do Brasil, em no máximo 2 frases curtas (a lente é pequena). "
    "Sem travessão. Nunca diga que mandou, comprou ou salvou algo: você não tem ferramentas "
    "aqui, então só responde e sugere. Responda SOMENTE com JSON assim: "
    '{"fala": "<resposta curta>", "tela": "<uma de: ' + ", ".join(TELAS) + '>"} '
    "escolhendo a tela que mais combina com o assunto (use claude se nenhuma combinar)."
)

CLAUDE = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
sessao = {"id": None}
ultimos = []  # limite simples de pedidos

def perguntar(texto, tela_atual):
    cmd = [CLAUDE, "-p", f"[tela atual: {tela_atual}] {texto}",
           "--output-format", "json", "--max-turns", "1",
           "--append-system-prompt", INSTRUCOES,
           "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit",
           "WebFetch", "WebSearch", "Task"]
    if sessao["id"]:
        cmd += ["--resume", sessao["id"]]
    # Sem chave de API: força o uso da assinatura, nunca cobrança por API.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    r = subprocess.run(cmd, cwd=TRABALHO, capture_output=True, text=True, timeout=90, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "claude falhou")
    saida = json.loads(r.stdout)
    sessao["id"] = saida.get("session_id") or sessao["id"]
    bruto = (saida.get("result") or "").strip()
    try:
        ini, fim = bruto.index("{"), bruto.rindex("}") + 1
        j = json.loads(bruto[ini:fim])
        fala, tela = str(j.get("fala", "")).strip(), str(j.get("tela", "claude"))
    except ValueError:
        fala, tela = bruto, "claude"
    if tela not in TELAS:
        tela = "claude"
    return {"fala": fala[:400] or "Não consegui responder agora.", "tela": tela}

class H(BaseHTTPRequestHandler):
    def _cors(self):
        if self.headers.get("Origin") == ORIGEM:
            self.send_header("Access-Control-Allow-Origin", ORIGEM)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Chave")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

    def _json(self, cod, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(cod); self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def _autorizado(self):
        return hmac.compare_digest(self.headers.get("X-Chave", ""), CHAVE)

    def do_GET(self):
        if self.path != "/ping":
            return self._json(404, {"erro": "não existe"})
        if not self._autorizado():
            return self._json(401, {"erro": "chave errada"})
        self._json(200, {"ok": True})

    def do_POST(self):
        if self.path != "/perguntar":
            return self._json(404, {"erro": "não existe"})
        if not self._autorizado():
            return self._json(401, {"erro": "chave errada"})
        agora = time.time()
        ultimos[:] = [t for t in ultimos if agora - t < 60]
        if len(ultimos) >= 20:
            return self._json(429, {"erro": "muitas perguntas por minuto"})
        ultimos.append(agora)
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 4000)
            d = json.loads(self.rfile.read(n) or b"{}")
            texto = str(d.get("texto", "")).strip()[:1000]
            if not texto:
                return self._json(400, {"erro": "sem texto"})
            self._json(200, perguntar(texto, str(d.get("tela", ""))[:30]))
        except subprocess.TimeoutExpired:
            self._json(504, {"erro": "o Claude demorou demais"})
        except Exception as e:
            self._json(500, {"erro": str(e)[:300]})

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

def tailscale():
    for c in (shutil.which("tailscale"), "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if c and os.path.exists(c):
            return c
    return None

def abrir_funnel():
    """Abre a porta da ponte na internet pelo Tailscale Funnel (grátis) e devolve o endereço."""
    ts = tailscale()
    if not ts:
        print("Não achei o Tailscale. Abra o app Tailscale e rode de novo.")
        return None
    try:
        st = json.loads(subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=20).stdout)
        nome = st["Self"]["DNSName"].rstrip(".")
    except Exception as e:
        print("Não consegui ler o nome do Mac no Tailscale:", e)
        return None
    r = subprocess.run([ts, "funnel", "--bg", str(PORTA)], capture_output=True, text=True, timeout=60)
    saida = (r.stdout + r.stderr).strip()
    if saida:
        print(saida)
    if r.returncode != 0:
        print("\nO Funnel ainda não está liberado. Abra o link acima, aprove e rode a ponte de novo.")
        return None
    return "https://" + nome

if __name__ == "__main__":
    print(f"Ponte CWB ligada em 127.0.0.1:{PORTA}")
    end = abrir_funnel()
    if end:
        app = f"https://aedchatgpt1.github.io/cwb-test-oculos/?ponte={end}&chave={CHAVE}"
        try:
            subprocess.run(["pbcopy"], input=app, text=True, timeout=5)
            print("\nPRONTO. O endereço do app com a chave já está copiado.")
            print("No iPhone, cole no app Meta AI, em Web Apps, no lugar do endereço atual.")
        except Exception:
            print("\nEndereço do app (cole no Meta AI):\n" + app)
    print("\nDeixe esta janela aberta. Para desligar: Control+C.")
    ThreadingHTTPServer(("127.0.0.1", PORTA), H).serve_forever()
