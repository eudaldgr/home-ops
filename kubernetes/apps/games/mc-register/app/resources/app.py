import html
import json
import os
import re
import secrets
import socket
import string
import struct
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ORIGIN = os.environ["APP_ORIGIN"].rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))
DATA_FILE = os.environ.get("DATA_FILE", "/data/requests.json")
MAX_PENDING = int(os.environ.get("MAX_PENDING", "50"))
MAX_PENDING_PER_IP = int(os.environ.get("MAX_PENDING_PER_IP", "3"))
RETENTION = 30 * 24 * 3600

JAVA_NAME = re.compile(r"[A-Za-z0-9_]{3,16}")
BEDROCK_NAME = re.compile(r"[A-Za-z0-9 ]{1,16}")
PLAYER = re.compile(r"\.?[A-Za-z0-9_]{1,16}")
PASSWORD = re.compile(r"[!-~]{5,30}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{16,64}")
COLOR = re.compile(r"§.")
ALPHABET = string.ascii_letters + string.digits
EDITIONS = {"java-premium": "Java (paid account)", "java": "Java (no account)", "bedrock": "Bedrock"}
TABS = {"register": "new", "password": "password", "premium": "manage", "freemium": "manage", "unregister": "manage"}


def load_servers():
    servers = {}
    for line in re.split(r"[\n,]", os.environ["SERVERS"]):
        parts = line.split()
        if not parts:
            continue
        name, _, address = parts[0].partition("=")
        host, _, port = address.rpartition(":")
        key = "RCON_PASSWORD_" + re.sub(r"[^A-Z0-9]", "_", name.upper())
        # authme: offline mode + AuthMe (Paper). fe: offline-mode Forge with ForgeEssentials auth; the whitelist and
        # passwords are files written by the server's agent sidecar (4th field: its URL, same token as RCON)
        mode = parts[2] if len(parts) > 2 else "authme"
        if mode not in ("authme", "fe") or (mode == "fe" and len(parts) < 4):
            raise ValueError(f"server {name}: mode must be authme, or fe followed by the agent URL")
        servers[name] = {"host": host, "port": int(port), "password": os.environ[key], "address": parts[1] if len(parts) > 1 else "",
                         "mode": mode, "agent": parts[3] if mode == "fe" else ""}
    return servers


def forge(server):
    return SERVERS[server]["mode"] == "fe"


def passwordless(request):
    # Only AuthMe can let paid accounts in without a password
    return request["edition"] == "java-premium" and SERVERS.get(request["server"], {}).get("mode") != "fe"


SERVERS = load_servers()


def rcon(server, command, timeout=10):
    target = SERVERS[server]
    with socket.create_connection((target["host"], target["port"]), timeout=timeout) as sock:

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

        send(1, 3, target["password"])
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


def run(server, commands):
    replies = [rcon(server, command) for command in commands]
    return "\n".join(reply for reply in replies if reply) or "Command sent. The server returned no message; check the server log if needed."


def agent(server, path, payload):
    target = SERVERS[server]
    request = urllib.request.Request(
        target["agent"].rstrip("/") + path, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {target['password']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["message"]


def fe_register(server, name, password):
    replies = [
        agent(server, "/whitelist", {"name": name, "add": True}),
        agent(server, "/password", {"name": name, "password": password}),
        rcon(server, "whitelist reload"),
    ]
    return "\n".join(reply for reply in replies if reply)


def fe_unregister(server, name):
    replies = [
        agent(server, "/whitelist", {"name": name, "add": False}),
        agent(server, "/password", {"name": name, "password": None}),
        rcon(server, "whitelist reload"),
    ]
    return "\n".join(reply for reply in replies if reply)


def generate_password():
    return "".join(secrets.choice(ALPHABET) for _ in range(12))


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()

    def load(self):
        try:
            with open(self.path) as file:
                requests = json.load(file)
        except FileNotFoundError:
            requests = {}
        cutoff = time.time() - RETENTION
        return {key: value for key, value in requests.items() if value["updated"] > cutoff}

    def save(self, requests):
        temp = self.path + ".tmp"
        with open(temp, "w") as file:
            json.dump(requests, file)
        os.replace(temp, self.path)


STORE = Store(DATA_FILE)


def player_name(edition, raw):
    raw = raw.strip()
    if edition == "bedrock":
        if not BEDROCK_NAME.fullmatch(raw):
            return None
        return ("." + raw.replace(" ", "_"))[:16]
    return raw if JAVA_NAME.fullmatch(raw) else None


def create_request(form, ip):
    if form.get("website"):
        return "ok", None
    server = form.get("server", "")
    edition = form.get("edition", "")
    if server not in SERVERS or edition not in EDITIONS:
        return "error", "Please choose a server and an edition."
    if forge(server) and edition == "bedrock":
        return "error", f"{server} is a modded Java server: Bedrock can't join."
    name = player_name(edition, form.get("name", ""))
    if not name:
        return "error", "Invalid name. Java: 3-16 letters, numbers or _. Bedrock: your Xbox gamertag."
    note = form.get("note", "").strip()[:300]
    with STORE.lock:
        requests = STORE.load()
        pending = [r for r in requests.values() if r["status"] == "pending"]
        if any(r["server"] == server and r["name"].lower() == name.lower() for r in pending):
            return "error", f"There is already a pending request for {name}."
        if len(pending) >= MAX_PENDING or sum(r["ip"] == ip for r in pending) >= MAX_PENDING_PER_IP:
            return "error", "Too many pending requests. Please try again later."
        token = secrets.token_urlsafe(24)
        now = time.time()
        requests[token] = {"server": server, "edition": edition, "name": name, "note": note, "ip": ip,
                           "status": "pending", "created": now, "updated": now, "password": None}
        STORE.save(requests)
    return "ok", token


def decide(token, decision):
    with STORE.lock:
        requests = STORE.load()
        request = requests.get(token)
        if not request or request["status"] != "pending":
            return "error", "This request no longer exists or was already handled.", None
        if decision == "reject":
            request.update(status="rejected", updated=time.time())
            STORE.save(requests)
            return "ok", f"Request from {request['name']} rejected.", None
        password = generate_password()
        try:
            if forge(request["server"]):
                output = fe_register(request["server"], request["name"], password)
            else:
                commands = [f"authme register {request['name']} {password}"]
                if request["edition"] == "java-premium":
                    commands.append(f"authme premium {request['name']}")
                output = run(request["server"], commands)
        except Exception as error:
            return "error", f"Could not reach {request['server']}: {error}", None
        request.update(status="approved", updated=time.time(), password=None if passwordless(request) else password)
        STORE.save(requests)
        return "ok", f"[{request['server']}] {request['name']} approved.\n{output}", password


def manage(form):
    server = form.get("server", "")
    if server not in SERVERS:
        return "error", "Unknown server.", None
    action = form.get("action", "")
    if TABS.get(action) != form.get("tab"):
        return "error", "Use the button of the active tab.", None
    if action == "unregister" and not form.get("confirm"):
        return "error", "Tick the confirmation box to remove the player.", None
    name = form.get("name", "").strip()
    if not PLAYER.fullmatch(name):
        return "error", "Invalid player name.", None
    if forge(server) and action in ("premium", "freemium"):
        return "error", f"{server} has no premium mode: everyone logs in with a password.", None
    password = form.get("password", "")
    generated = None
    if action in ("register", "password"):
        if not password:
            password = generated = generate_password()
        if not PASSWORD.fullmatch(password):
            return "error", "Invalid password: 5-30 characters, no spaces.", None
    commands = {
        "register": [f"authme register {name} {password}"] + ([f"authme premium {name}"] if form.get("premium") else []),
        "password": [f"authme password {name} {password}"],
        "unregister": [f"authme unregister {name}"],
        "premium": [f"authme premium {name}"],
        "freemium": [f"authme freemium {name}"],
    }[action]
    try:
        if forge(server):
            output = {
                "register": lambda: fe_register(server, name, password),
                "password": lambda: agent(server, "/password", {"name": name, "password": password}),
                "unregister": lambda: fe_unregister(server, name),
            }[action]()
        else:
            output = run(server, commands)
        return "ok", f"[{server}] {output}", generated
    except Exception as error:
        return "error", f"Could not reach {server}: {error}", None


STYLE = """
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
main { max-width: 36rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
header { display: flex; align-items: center; gap: .75rem; margin-bottom: 1.5rem; }
.logo { width: 2.5rem; height: 2.5rem; border-radius: .5rem; background: linear-gradient(var(--accent) 62%, #866043 62%); flex: none; }
h1 { font-size: 1.35rem; margin: 0; line-height: 1.2; }
h2 { font-size: 1rem; margin: 0 0 .75rem; }
header p { margin: 0; color: var(--muted); font-size: .9rem; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 1rem; padding: 1.25rem; box-shadow: 0 1px 2px #0000000d; margin-bottom: 1rem; }
.result { margin-bottom: 1rem; border-radius: .75rem; padding: .9rem 1rem; }
.result.ok { background: var(--ok-bg); border: 1px solid var(--accent); }
.result.error { background: var(--err-bg); border: 1px solid var(--danger); }
.result pre { margin: 0; white-space: pre-wrap; font: inherit; }
.secret { display: flex; align-items: center; gap: .5rem; margin-top: .75rem; flex-wrap: wrap; }
.secret code, .link { font-size: 1.05rem; padding: .3rem .6rem; border-radius: .4rem; background: var(--card); border: 1px dashed var(--line); user-select: all; word-break: break-all; }
.secret small { width: 100%; color: var(--muted); }
.tabs { display: flex; gap: .25rem; padding: .25rem; background: var(--bg); border-radius: .75rem; margin-bottom: 1.25rem; }
.tabs label { flex: 1; text-align: center; padding: .5rem .25rem; border-radius: .55rem; cursor: pointer; color: var(--muted); font-weight: 600; font-size: .85rem; }
form > input[type=radio], .shell > input[type=radio] { position: absolute; opacity: 0; pointer-events: none; }
.field { display: grid; gap: .3rem; margin-bottom: 1rem; }
.field span { font-weight: 600; font-size: .9rem; }
.field small, .hint { color: var(--muted); font-size: .8rem; }
input[type=text], select, textarea {
  font: inherit; color: inherit; width: 100%; padding: .6rem .75rem; border-radius: .6rem;
  border: 1px solid var(--line); background: var(--bg);
}
textarea { resize: vertical; min-height: 4.5rem; }
input:focus, select:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; border-color: transparent; }
.check { display: flex; gap: .6rem; align-items: flex-start; margin-bottom: 1rem; cursor: pointer; }
.check input { margin-top: .3rem; accent-color: var(--accent); }
button { font: inherit; font-weight: 600; border: 0; border-radius: .6rem; padding: .65rem 1rem; cursor: pointer; }
.primary { background: var(--accent); color: var(--accent-ink); width: 100%; }
.secondary { background: var(--bg); color: var(--text); border: 1px solid var(--line); }
.danger { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
button:hover { filter: brightness(1.07); }
.row { display: flex; gap: .5rem; flex-wrap: wrap; }
.row button, .row form { flex: 1; }
.panel { display: none; }
.request { border-top: 1px solid var(--line); padding: .9rem 0; }
.request:first-of-type { border-top: 0; padding-top: 0; }
.request strong { font-size: 1.05rem; }
.meta { color: var(--muted); font-size: .85rem; }
.note { margin: .4rem 0 .6rem; padding: .4rem .6rem; border-left: 3px solid var(--line); white-space: pre-wrap; }
.badge { display: inline-block; font-size: .75rem; font-weight: 600; padding: .1rem .5rem; border-radius: 1rem; background: var(--bg); border: 1px solid var(--line); }
.badge.approved { border-color: var(--accent); color: var(--accent); }
.badge.rejected { border-color: var(--danger); color: var(--danger); }
hr { border: 0; border-top: 1px solid var(--line); margin: 1.25rem 0; }
footer { margin-top: 1.5rem; color: var(--muted); font-size: .8rem; text-align: center; }
footer a { color: var(--muted); }
"""

ADMIN_TABS = """
#tab-requests:checked ~ .tabs label[for=tab-requests], #tab-new:checked ~ .tabs label[for=tab-new],
#tab-password:checked ~ .tabs label[for=tab-password], #tab-manage:checked ~ .tabs label[for=tab-manage] {
  background: var(--card); color: var(--text); box-shadow: 0 1px 2px #0000001a; }
#tab-requests:checked ~ .p-requests, #tab-new:checked ~ form .p-new, #tab-password:checked ~ form .p-password,
#tab-manage:checked ~ form .p-manage { display: block; }
#tab-new:checked ~ form .p-secret, #tab-password:checked ~ form .p-secret { display: grid; }
#tab-requests:checked ~ form { display: none; }
"""


def esc(value):
    return html.escape(str(value))


def ago(timestamp):
    minutes = int((time.time() - timestamp) / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 1440:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} d ago"


def layout(nonce, title, subtitle, body, extra_style="", copy=False):
    script = ""
    if copy:
        script = (
            f'<script nonce="{nonce}">document.querySelectorAll("[data-copy]").forEach(b=>b.addEventListener("click",()=>'
            'navigator.clipboard.writeText(b.dataset.copy).then(()=>{b.textContent="Copied"})))</script>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{esc(title)}</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' fill='%235b8731'/%3E%3Crect y='10' width='16' height='6' fill='%23866043'/%3E%3C/svg%3E">
<style>{STYLE}{extra_style}</style>
</head>
<body>
<main>
<header><div class="logo"></div><div><h1>{esc(title)}</h1><p>{esc(subtitle)}</p></div></header>
{body}
</main>
{script}
</body>
</html>"""


def result_box(status, message, password=None, link=None):
    if not message:
        return ""
    box = f'<div class="result {status}"><pre>{esc(message)}</pre>'
    if link:
        box += f'<div class="secret"><span class="link">{esc(link)}</span><button type="button" class="secondary" data-copy="{esc(link)}">Copy</button></div>'
    if password:
        box += (
            f'<div class="secret"><code>{esc(password)}</code>'
            f'<button type="button" class="secondary" data-copy="{esc(password)}">Copy</button>'
            "<small>Copy it now: it will not be shown again here.</small></div>"
        )
    return box + "</div>"


def server_options(selected=None, with_state=False):
    states = {}
    if with_state:
        with ThreadPoolExecutor() as pool:
            states = dict(zip(SERVERS, pool.map(online, SERVERS)))
    selected = selected or next(iter(SERVERS))
    options = []
    for name, server in SERVERS.items():
        label = name + (f" ({server['address']})" if server["address"] else "")
        if with_state:
            label += " · online" if states[name] else " · offline"
        options.append(f'<option value="{esc(name)}"{" selected" if name == selected else ""}>{esc(label)}</option>')
    return "".join(options)


def public_page(nonce, status=None, message=None, link=None):
    editions = "".join(f'<option value="{key}">{esc(label)}</option>' for key, label in EDITIONS.items())
    modded = ", ".join(name for name in SERVERS if forge(name))
    modded_hint = f" {modded}: Java only, and everyone gets a password (paid accounts too)." if modded else ""
    body = f"""{result_box(status, message, link=link)}
<form method="post" action="/request" class="card">
  <h2>Request access</h2>
  <label class="field"><span>Server</span><select name="server">{server_options()}</select></label>
  <label class="field"><span>Edition</span><select name="edition">{editions}</select>
    <small>Paid Java accounts join without a password. Everyone else gets a password when approved.{esc(modded_hint)}</small></label>
  <label class="field"><span>Player name</span>
    <input type="text" name="name" required maxlength="16" autocomplete="off" spellcheck="false" placeholder="Java username or Xbox gamertag"></label>
  <label class="field"><span>Who are you?</span>
    <textarea name="note" maxlength="300" placeholder="So the admin knows who is asking"></textarea></label>
  <input type="text" name="website" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px" aria-hidden="true">
  <button class="primary">Send request</button>
</form>
<footer>Your request will be reviewed by the server admin.</footer>"""
    return layout(nonce, "Minecraft access", "Ask to join the server", body, copy=bool(link))


def status_page(nonce, token, request, reveal=False):
    server = SERVERS.get(request["server"], {})
    address = server.get("address") or request["server"]
    if request["status"] == "pending":
        content = f"<p>Your request for <strong>{esc(request['name'])}</strong> is waiting for approval. Check this page again later.</p>"
    elif request["status"] == "rejected":
        content = f"<p>Your request for <strong>{esc(request['name'])}</strong> was not approved.</p>"
    else:
        content = f"<p>Your request for <strong>{esc(request['name'])}</strong> was approved. Connect to <strong>{esc(address)}</strong>.</p>"
        login = "/auth login <password>" if server.get("mode") == "fe" else "/login <password>"
        if server.get("mode") == "fe":
            content += f"<p class='hint'>Use exactly <strong>{esc(request['name'])}</strong> as your username: capital letters matter.</p>"
        if passwordless(request):
            content += "<p class='hint'>You have a paid Java account: just join, no password needed.</p>"
        elif reveal and request.get("password"):
            content += result_box("ok", f"Your password. Log in with {login} when you join.", password=request["password"])
        elif request.get("password"):
            content += (
                f'<form method="post" action="/status/{esc(token)}"><button class="primary">Show my password</button></form>'
                "<p class='hint'>The password is shown only once.</p>"
            )
        else:
            content += "<p class='hint'>Your password was already shown. Ask the admin if you lost it.</p>"
    body = f'<div class="card"><h2>Request status <span class="badge {esc(request["status"])}">{esc(request["status"])}</span></h2>{content}</div>'
    return layout(nonce, "Minecraft access", "Request status", body, copy=reveal)


def admin_page(nonce, status=None, message=None, password=None, form=None):
    form = form or {}
    action = form.get("action", "")
    tab = "requests" if form.get("decision") else TABS.get(action, form.get("tab", "requests"))
    if tab not in ("requests", "new", "password", "manage"):
        tab = "requests"
    with STORE.lock:
        requests = STORE.load()
    pending = sorted(((k, v) for k, v in requests.items() if v["status"] == "pending"), key=lambda item: item[1]["created"])
    recent = sorted((v for v in requests.values() if v["status"] != "pending"), key=lambda v: -v["updated"])[:10]

    items = []
    for token, request in pending:
        note = f'<div class="note">{esc(request["note"])}</div>' if request["note"] else ""
        items.append(f"""<div class="request">
  <strong>{esc(request['name'])}</strong> <span class="badge">{esc(EDITIONS[request['edition']])}</span>
  <div class="meta">{esc(request['server'])} · {ago(request['created'])}</div>{note}
  <form method="post" action="/admin/request" class="row">
    <input type="hidden" name="token" value="{esc(token)}">
    <button class="primary" name="decision" value="approve">Approve</button>
    <button class="danger" name="decision" value="reject">Reject</button>
  </form>
</div>""")
    pending_html = "".join(items) or "<p class='hint'>No pending requests.</p>"
    recent_html = "".join(
        f'<div class="meta">{esc(r["name"])} · {esc(r["server"])} · <span class="badge {esc(r["status"])}">{esc(r["status"])}</span> {ago(r["updated"])}</div>'
        for r in recent
    )
    if recent_html:
        recent_html = f"<hr><h2>Recent decisions</h2>{recent_html}"

    modded = ", ".join(name for name in SERVERS if forge(name))
    modded_hint = (
        f"\n      <small>{esc(modded)}: ForgeEssentials accounts (whitelist + password). Names are case-sensitive; premium does not apply.</small>"
        if modded else ""
    )
    checked = {key: " checked" if tab == key else "" for key in ("requests", "new", "password", "manage")}
    count = f" ({len(pending)})" if pending else ""
    body = f"""{result_box(status, message, password)}
<div class="card shell">
  <input type="radio" name="tab" id="tab-requests" value="requests"{checked['requests']}>
  <input type="radio" name="tab" id="tab-new" value="new"{checked['new']}>
  <input type="radio" name="tab" id="tab-password" value="password"{checked['password']}>
  <input type="radio" name="tab" id="tab-manage" value="manage"{checked['manage']}>
  <div class="tabs">
    <label for="tab-requests">Requests{count}</label>
    <label for="tab-new">New player</label>
    <label for="tab-password">Password</label>
    <label for="tab-manage">Manage</label>
  </div>
  <div class="panel p-requests">{pending_html}{recent_html}</div>
  <form method="post" action="/admin">
    <input type="hidden" name="tab" value="{esc(tab if tab != 'requests' else 'new')}" class="tabsync">
    <label class="field"><span>Server</span><select name="server">{server_options(form.get("server"), with_state=True)}</select></label>
    <label class="field"><span>Player</span>
      <input type="text" name="name" autocomplete="off" spellcheck="false" value="{esc(form.get('name', '')) if status == 'error' or tab != 'new' else ''}" placeholder="Steve or .BedrockGamertag">
      <small>Java: the username. Bedrock: the gamertag with a leading dot.</small>{modded_hint}</label>
    <label class="field panel p-secret"><span>Password</span>
      <input type="text" name="password" autocomplete="off" spellcheck="false" placeholder="Leave empty to generate one"></label>
    <div class="panel p-new">
      <label class="check"><input type="checkbox" name="premium" value="1">
        <span>Has a paid Minecraft account (premium)<br><small class="hint">Joins without a password; AuthMe verifies it with Mojang.</small></span></label>
      <button class="primary" name="action" value="register">Register player</button>
    </div>
    <div class="panel p-password"><button class="primary" name="action" value="password">Change password</button></div>
    <div class="panel p-manage">
      <p class="hint">Premium mode: the player joins without a password if they own Minecraft.</p>
      <div class="row">
        <button class="secondary" name="action" value="premium">Enable premium</button>
        <button class="secondary" name="action" value="freemium">Disable premium</button>
      </div>
      <hr>
      <label class="check"><input type="checkbox" name="confirm" value="1">
        <span>I want to remove this player<br><small class="hint">Deletes the AuthMe account; they will need to be registered again.</small></span></label>
      <button class="danger" name="action" value="unregister">Remove player</button>
    </div>
  </form>
</div>
<footer><a href="/">Public request page</a> · <a href="/oauth2/logout">Log out</a></footer>"""
    sync = (
        f'<script nonce="{nonce}">document.querySelectorAll(".shell > input[type=radio]").forEach(r=>r.addEventListener("change",()=>'
        'document.querySelector(".tabsync").value=r.value))</script>'
    )
    return layout(nonce, "Minecraft admin", "Requests and player accounts", body + sync, ADMIN_TABS, copy=bool(password))


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body, content_type="text/html; charset=utf-8", nonce="", location=None):
        data = body.encode()
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'none'; style-src 'unsafe-inline'; img-src data:; script-src 'nonce-{nonce}'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def html(self, render, *args, **kwargs):
        nonce = secrets.token_urlsafe(16)
        self.reply(200, render(nonce, *args, **kwargs), nonce=nonce)

    def client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or self.client_address[0]

    def form(self):
        origin = self.headers.get("Origin") or ""
        if origin != ORIGIN:
            return None
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return None
        return {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}

    def status_request(self):
        token = self.path.removeprefix("/status/")
        if not TOKEN.fullmatch(token):
            return None, None
        with STORE.lock:
            return token, STORE.load().get(token)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.reply(200, "ok", "text/plain")
        if path == "/":
            return self.html(public_page)
        if path == "/admin":
            return self.html(admin_page)
        if path.startswith("/status/"):
            token, request = self.status_request()
            if request:
                return self.html(status_page, token, request)
        self.reply(404, "not found", "text/plain")

    def do_POST(self):
        path = self.path.split("?")[0]
        form = self.form()
        if form is None:
            return self.reply(403, "forbidden", "text/plain")
        if path == "/request":
            status, value = create_request(form, self.client_ip())
            if status == "error":
                return self.html(public_page, status, value)
            if value is None:
                return self.html(public_page, "ok", "Request sent.")
            link = f"{ORIGIN}/status/{value}"
            return self.html(public_page, "ok", "Request sent. Save this link to check its status and get your password once it is approved:", link=link)
        if path.startswith("/status/"):
            with STORE.lock:
                token = path.removeprefix("/status/")
                requests = STORE.load()
                request = requests.get(token) if TOKEN.fullmatch(token) else None
                if not request:
                    return self.reply(404, "not found", "text/plain")
                shown = dict(request)
                if request["status"] == "approved" and request.get("password"):
                    request["password"] = None
                    request["updated"] = time.time()
                    STORE.save(requests)
            return self.html(status_page, token, shown, reveal=True)
        if path == "/admin/request":
            decision = form.get("decision")
            if decision not in ("approve", "reject"):
                return self.reply(400, "bad request", "text/plain")
            status, message, password = decide(form.get("token", ""), decision)
            return self.html(admin_page, status, message, password, form)
        if path == "/admin":
            status, message, password = manage(form)
            return self.html(admin_page, status, message, password, form)
        self.reply(404, "not found", "text/plain")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
