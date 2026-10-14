import html
import os
import re
import secrets
import socket
import string
import struct
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ORIGIN = os.environ["APP_ORIGIN"].rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))

NAME = re.compile(r"\.?[A-Za-z0-9_]{3,16}")
PASSWORD = re.compile(r"[!-~]{5,30}")
COLOR = re.compile(r"§.")
ALPHABET = string.ascii_letters + string.digits
TABS = {"register": "new", "password": "password", "premium": "manage", "freemium": "manage", "unregister": "manage"}


def load_servers():
    servers = {}
    for line in re.split(r"[\n,]", os.environ["SERVERS"]):
        line = line.strip()
        if not line:
            continue
        name, _, address = line.partition("=")
        host, _, port = address.rpartition(":")
        key = "RCON_PASSWORD_" + re.sub(r"[^A-Z0-9]", "_", name.upper())
        servers[name] = (host, int(port), os.environ[key])
    return servers


SERVERS = load_servers()


def rcon(server, command, timeout=10):
    host, port, password = SERVERS[server]
    with socket.create_connection((host, port), timeout=timeout) as sock:

        def read(size):
            data = b""
            while len(data) < size:
                chunk = sock.recv(size - len(data))
                if not chunk:
                    raise ConnectionError("connection closed")
                data += chunk
            return data

        def send(request_id, kind, body):
            payload = struct.pack("<ii", request_id, kind) + body.encode() + b"\x00\x00"
            sock.sendall(struct.pack("<i", len(payload)) + payload)

        def receive():
            (size,) = struct.unpack("<i", read(4))
            request_id, _ = struct.unpack("<ii", read(8))
            return request_id, read(size - 8)[:-2].decode(errors="replace")

        send(1, 3, password)
        if receive()[0] == -1:
            raise PermissionError("authentication failed")
        if command is None:
            return ""
        send(2, 2, command)
        return COLOR.sub("", receive()[1]).strip()


def online(server):
    try:
        rcon(server, None, timeout=2)
        return True
    except Exception:
        return False


def handle(form):
    server = form.get("server", "")
    if server not in SERVERS:
        return "error", "Servidor desconegut.", None
    action = form.get("action", "")
    if TABS.get(action) != form.get("tab"):
        return "error", "Fes servir el botó de la pestanya activa.", None
    if action == "unregister" and not form.get("confirm"):
        return "error", "Marca la casella de confirmació per donar de baixa el jugador.", None
    name = form.get("name", "").strip()
    if not NAME.fullmatch(name):
        return "error", "Nom no vàlid: 3-16 caràcters (lletres, números o _), amb un punt al davant si és de Bedrock.", None

    password = form.get("password", "")
    generated = None
    if action in ("register", "password"):
        if not password:
            password = generated = "".join(secrets.choice(ALPHABET) for _ in range(12))
        if not PASSWORD.fullmatch(password):
            return "error", "Contrasenya no vàlida: 5-30 caràcters, sense espais.", None

    commands = {
        "register": [f"authme register {name} {password}"] + ([f"authme premium {name}"] if form.get("premium") else []),
        "password": [f"authme password {name} {password}"],
        "unregister": [f"authme unregister {name}"],
        "premium": [f"authme premium {name}"],
        "freemium": [f"authme freemium {name}"],
    }.get(action)
    if not commands:
        return "error", "Acció desconeguda.", None

    try:
        replies = [rcon(server, command) for command in commands]
    except Exception as error:
        return "error", f"No s'ha pogut parlar amb {server}: {error}", None
    output = "\n".join(reply for reply in replies if reply)
    return "ok", output or "Ordre enviada. AuthMe no ha retornat cap missatge: si cal, comprova-ho al log del servidor.", generated


TEMPLATE = """<!doctype html>
<html lang="ca">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minecraft · Comptes</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%235b8731'/%3E%3Crect y='10' width='16' height='6' fill='%23866043'/%3E%3C/svg%3E">
<style>
:root {
  color-scheme: light dark;
  --bg: #f4f6f1; --card: #ffffff; --text: #1d2318; --muted: #5d6657; --line: #dde3d6;
  --accent: #5b8731; --accent-ink: #ffffff; --danger: #b3261e; --ok-bg: #e7f2dc; --err-bg: #fbe4e2;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #14170f; --card: #1d2118; --text: #e8ecdf; --muted: #a2ab98; --line: #2f3628;
          --accent: #7cb342; --accent-ink: #10140b; --danger: #ef6b62; --ok-bg: #1f2d15; --err-bg: #3a1a17; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 34rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
header { display: flex; align-items: center; gap: .75rem; margin-bottom: 1.5rem; }
.logo { width: 2.5rem; height: 2.5rem; border-radius: .5rem; background: linear-gradient(var(--accent) 62%, #866043 62%); flex: none; }
h1 { font-size: 1.35rem; margin: 0; line-height: 1.2; }
header p { margin: 0; color: var(--muted); font-size: .9rem; }
footer a { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 1rem; padding: 1.25rem; box-shadow: 0 1px 2px #0000000d; }
.result { margin-bottom: 1rem; border-radius: .75rem; padding: .9rem 1rem; }
.result.ok { background: var(--ok-bg); border: 1px solid var(--accent); }
.result.error { background: var(--err-bg); border: 1px solid var(--danger); }
.result pre { margin: 0; white-space: pre-wrap; font: inherit; }
.secret { display: flex; align-items: center; gap: .5rem; margin-top: .75rem; flex-wrap: wrap; }
.secret code { font-size: 1.1rem; padding: .3rem .6rem; border-radius: .4rem; background: var(--card); border: 1px dashed var(--line); user-select: all; }
.secret small { width: 100%; color: var(--muted); }
.tabs { display: flex; gap: .25rem; padding: .25rem; background: var(--bg); border-radius: .75rem; margin-bottom: 1.25rem; }
form > input[type=radio] { position: absolute; opacity: 0; pointer-events: none; }
.tabs label { flex: 1; text-align: center; padding: .5rem; border-radius: .55rem; cursor: pointer; color: var(--muted); font-weight: 600; font-size: .9rem; }
#tab-new:focus-visible ~ .tabs label[for=tab-new], #tab-password:focus-visible ~ .tabs label[for=tab-password], #tab-manage:focus-visible ~ .tabs label[for=tab-manage] { outline: 2px solid var(--accent); }
#tab-new:checked ~ .tabs label[for=tab-new],
#tab-password:checked ~ .tabs label[for=tab-password],
#tab-manage:checked ~ .tabs label[for=tab-manage] { background: var(--card); color: var(--text); box-shadow: 0 1px 2px #0000001a; }
.field { display: grid; gap: .3rem; margin-bottom: 1rem; }
.field span { font-weight: 600; font-size: .9rem; }
.field small, .hint { color: var(--muted); font-size: .8rem; }
input[type=text], input[type=password], select {
  font: inherit; color: inherit; width: 100%; padding: .6rem .75rem; border-radius: .6rem;
  border: 1px solid var(--line); background: var(--bg);
}
input:focus, select:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: transparent; }
.check { display: flex; gap: .6rem; align-items: flex-start; margin-bottom: 1rem; cursor: pointer; }
.check input { margin-top: .3rem; accent-color: var(--accent); }
button { font: inherit; font-weight: 600; border: 0; border-radius: .6rem; padding: .65rem 1rem; cursor: pointer; }
.primary { background: var(--accent); color: var(--accent-ink); width: 100%; }
.secondary { background: var(--bg); color: var(--text); border: 1px solid var(--line); }
.danger { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
button:hover { filter: brightness(1.07); }
.row { display: flex; gap: .5rem; flex-wrap: wrap; }
.row button { flex: 1; }
.panel { display: none; }
#tab-new:checked ~ .p-new, #tab-password:checked ~ .p-password, #tab-manage:checked ~ .p-manage { display: block; }
#tab-new:checked ~ .p-secret, #tab-password:checked ~ .p-secret { display: grid; }
hr { border: 0; border-top: 1px solid var(--line); margin: 1.25rem 0; }
footer { margin-top: 1.5rem; color: var(--muted); font-size: .8rem; text-align: center; }
</style>
</head>
<body>
<main>
<header>
  <div class="logo"></div>
  <div><h1>Comptes de Minecraft</h1><p>Registre i gestió d'AuthMe</p></div>
</header>
@@RESULT@@
<form method="post" class="card">
  <input type="radio" name="tab" id="tab-new" value="new"@@NEW@@>
  <input type="radio" name="tab" id="tab-password" value="password"@@PASSWORD@@>
  <input type="radio" name="tab" id="tab-manage" value="manage"@@MANAGE@@>
  <div class="tabs">
    <label for="tab-new">Nou jugador</label>
    <label for="tab-password">Contrasenya</label>
    <label for="tab-manage">Gestió</label>
  </div>
  <label class="field">
    <span>Servidor</span>
    <select name="server">@@SERVERS@@</select>
  </label>
  <label class="field">
    <span>Jugador</span>
    <input type="text" name="name" required autocomplete="off" spellcheck="false" value="@@NAME@@" placeholder="Steve o .GamertagBedrock">
    <small>Java: el nom de jugador. Bedrock: el gamertag amb un punt al davant.</small>
  </label>
  <label class="field panel p-secret">
    <span>Contrasenya</span>
    <input type="text" name="password" autocomplete="off" spellcheck="false" placeholder="Deixa-la buida i se'n genera una">
  </label>
  <div class="panel p-new">
    <label class="check">
      <input type="checkbox" name="premium" value="1">
      <span>Té compte de Minecraft (premium)<br><small class="hint">Entrarà directament, sense contrasenya. AuthMe ho verifica amb Mojang.</small></span>
    </label>
    <button class="primary" name="action" value="register">Registrar jugador</button>
  </div>
  <div class="panel p-password">
    <button class="primary" name="action" value="password">Canviar contrasenya</button>
  </div>
  <div class="panel p-manage">
    <p class="hint">Mode premium: el jugador entra sense contrasenya si té compte de Minecraft.</p>
    <div class="row">
      <button class="secondary" name="action" value="premium">Activar premium</button>
      <button class="secondary" name="action" value="freemium">Desactivar premium</button>
    </div>
    <hr>
    <label class="check">
      <input type="checkbox" name="confirm" value="1">
      <span>Confirmo que vull donar-lo de baixa<br><small class="hint">S'esborra el compte d'AuthMe i haurà de tornar a ser registrat.</small></span>
    </label>
    <button class="danger" name="action" value="unregister">Donar de baixa</button>
  </div>
</form>
<footer>Els jugadors no registrats no poden entrar al servidor. · <a href="/oauth2/logout">Tancar sessió</a></footer>
</main>
@@SCRIPT@@
</body>
</html>"""


def page(nonce, status=None, message=None, password=None, form=None):
    form = form or {}
    selected = form.get("server") or next(iter(SERVERS))
    tab = TABS.get(form.get("action", ""), form.get("tab", "new"))
    if tab not in ("new", "password", "manage"):
        tab = "new"

    with ThreadPoolExecutor() as pool:
        states = dict(zip(SERVERS, pool.map(online, SERVERS)))
    servers = "".join(
        f'<option value="{html.escape(name)}"{" selected" if name == selected else ""}>'
        f'{html.escape(name)} · {"en línia" if up else "fora de línia"}</option>'
        for name, up in states.items()
    )

    result = ""
    if message:
        result = f'<div class="result {status}"><pre>{html.escape(message)}</pre>'
        if password:
            result += (
                f'<div class="secret"><code>{html.escape(password)}</code>'
                f'<button type="button" class="secondary" data-copy="{html.escape(password)}">Copiar</button>'
                "<small>Contrasenya generada. Copia-la ara: no es tornarà a mostrar.</small></div>"
            )
        result += "</div>"

    script = ""
    if password:
        script = (
            f'<script nonce="{nonce}">document.querySelectorAll("[data-copy]").forEach(b=>b.addEventListener("click",()=>'
            'navigator.clipboard.writeText(b.dataset.copy).then(()=>{b.textContent="Copiada"})))</script>'
        )

    keep_name = status == "error" or tab != "new"
    return (
        TEMPLATE.replace("@@RESULT@@", result)
        .replace("@@SERVERS@@", servers)
        .replace("@@NAME@@", html.escape(form.get("name", "")) if keep_name else "")
        .replace("@@NEW@@", " checked" if tab == "new" else "")
        .replace("@@PASSWORD@@", " checked" if tab == "password" else "")
        .replace("@@MANAGE@@", " checked" if tab == "manage" else "")
        .replace("@@SCRIPT@@", script)
    )


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type="text/html; charset=utf-8", nonce=""):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'nonce-{nonce}'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, "ok", "text/plain")
        if self.path != "/":
            return self.reply(404, "not found", "text/plain")
        nonce = secrets.token_urlsafe(16)
        self.reply(200, page(nonce), nonce=nonce)

    def do_POST(self):
        if self.path != "/":
            return self.reply(404, "not found", "text/plain")
        origin = self.headers.get("Origin") or self.headers.get("Referer", "")
        if origin != ORIGIN and not origin.startswith(ORIGIN + "/"):
            return self.reply(403, "forbidden", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self.reply(413, "too large", "text/plain")
        form = {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}
        status, message, password = handle(form)
        nonce = secrets.token_urlsafe(16)
        self.reply(200, page(nonce, status, message, password, form), nonce=nonce)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
