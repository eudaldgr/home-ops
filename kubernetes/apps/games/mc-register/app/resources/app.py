import html
import os
import re
import secrets
import socket
import string
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ORIGIN = os.environ["APP_ORIGIN"].rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))

NAME = re.compile(r"\.?[A-Za-z0-9_]{3,16}")
PASSWORD = re.compile(r"[!-~]{5,30}")
COLOR = re.compile(r"§.")
ALPHABET = string.ascii_letters + string.digits


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


def rcon(server, command):
    host, port, password = SERVERS[server]
    with socket.create_connection((host, port), timeout=10) as sock:

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
        send(2, 2, command)
        return COLOR.sub("", receive()[1]).strip()


def handle(form):
    server = form.get("server", "")
    if server not in SERVERS:
        return "error", "Servidor desconegut.", None
    action = form.get("action", "")
    name = form.get("name", "").strip()
    if not NAME.fullmatch(name):
        return "error", "Nom de jugador no vàlid (3-16 caràcters: lletres, números o _, amb un punt al davant si és de Bedrock).", None

    password = form.get("password", "")
    generated = None
    if action in ("register", "password"):
        if not password:
            password = generated = "".join(secrets.choice(ALPHABET) for _ in range(12))
        if not PASSWORD.fullmatch(password):
            return "error", "Contrasenya no vàlida (5-30 caràcters, sense espais).", None

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
        output = "\n".join(rcon(server, command) or "(sense resposta)" for command in commands)
    except Exception as error:
        return "error", f"Error de connexió amb {server}: {error}", None
    return "ok", f"[{server}] {output}", generated


def options(selected):
    return "".join(
        f'<option value="{html.escape(name)}"{" selected" if name == selected else ""}>{html.escape(name)}</option>'
        for name in SERVERS
    )


def page(status=None, message=None, password=None, server=None):
    result = ""
    if message:
        result = f'<div class="{status}"><pre>{html.escape(message)}</pre>'
        if password:
            result += f'<p>Contrasenya generada (copia-la ara, no es tornarà a mostrar): <code>{html.escape(password)}</code></p>'
        result += "</div>"
    select = f'<label>Servidor <select name="server">{options(server or next(iter(SERVERS)))}</select></label>'
    return f"""<!doctype html>
<html lang="ca">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minecraft · comptes</title>
<style>
:root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
body {{ max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
form {{ border: 1px solid #8884; border-radius: .5rem; padding: 1rem; margin-bottom: 1rem; display: grid; gap: .5rem; }}
input, select, button {{ font: inherit; padding: .4rem; }}
.ok {{ border-left: 4px solid #2a2; padding: .5rem 1rem; }}
.error {{ border-left: 4px solid #c22; padding: .5rem 1rem; }}
pre {{ white-space: pre-wrap; margin: 0; }}
small {{ opacity: .7; }}
</style>
</head>
<body>
<h1>Comptes de Minecraft</h1>
{result}
<form method="post">
<h2>Registrar jugador</h2>
{select}
<label>Nom <input name="name" required placeholder="nom o .GamertagBedrock"></label>
<label>Contrasenya <input name="password" placeholder="buida = se'n genera una"></label>
<label><input type="checkbox" name="premium" value="1"> Té compte premium (entrarà sense contrasenya)</label>
<button name="action" value="register">Registrar</button>
</form>
<form method="post">
<h2>Canviar contrasenya</h2>
{select}
<label>Nom <input name="name" required></label>
<label>Contrasenya nova <input name="password" placeholder="buida = se'n genera una"></label>
<button name="action" value="password">Canviar</button>
</form>
<form method="post">
<h2>Premium i baixes</h2>
{select}
<label>Nom <input name="name" required></label>
<button name="action" value="premium">Activar premium</button>
<button name="action" value="freemium">Desactivar premium</button>
<button name="action" value="unregister">Donar de baixa</button>
</form>
<p><small>Bedrock: el nom és el gamertag amb un punt al davant (.Gamertag). <a href="/oauth2/logout">Tancar sessió</a></small></p>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, "ok", "text/plain")
        if self.path != "/":
            return self.reply(404, "not found", "text/plain")
        self.reply(200, page())

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
        self.reply(200, page(status, message, password, form.get("server")))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
