import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Sidecar for mc-register: writes the files that RCON can't handle on an offline-mode Forge 1.7.10 server.
# - whitelist.json with the offline UUID (vanilla `whitelist add` asks Mojang first and stores the online UUID)
# - ForgeEssentials AuthLogin passwords (`auth setpass` from the console only works while the player is online)
# After a whitelist change, mc-register sends `whitelist reload` over RCON.

DATA = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8081"))
TOKEN = os.environ["AGENT_TOKEN"]
SALT = os.environ["FE_SALT"]
NAME = re.compile(r"[A-Za-z0-9_]{1,16}")
LOCK = threading.Lock()


def offline_uuid(name):
    # Same as Java UUID.nameUUIDFromBytes("OfflinePlayer:" + name)
    return str(uuid.UUID(bytes=hashlib.md5(f"OfflinePlayer:{name}".encode()).digest(), version=3))


def world():
    try:
        with open(f"{DATA}/server.properties") as file:
            for line in file:
                if line.startswith("level-name="):
                    return line.split("=", 1)[1].strip() or "world"
    except FileNotFoundError:
        pass
    return "world"


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w") as file:
        json.dump(data, file, indent=2)
    os.replace(temp, path)


def whitelist(name, add):
    path = f"{DATA}/whitelist.json"
    try:
        with open(path) as file:
            entries = json.load(file)
    except FileNotFoundError:
        entries = []
    entries = [entry for entry in entries if entry.get("name", "").lower() != name.lower()]
    if add:
        entries.append({"uuid": offline_uuid(name), "name": name})
    write_json(path, entries)
    return f"{name} {'added to' if add else 'removed from'} whitelist.json"


def password(name, value):
    # ForgeEssentials DataManager: <world>/FEData/json/PlayerPassData/<uuid>.json, SHA1(password + salt)
    path = f"{DATA}/{world()}/FEData/json/PlayerPassData/{offline_uuid(name)}.json"
    if value is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return f"Password removed for {name}"
    write_json(path, {"password": hashlib.sha1((value + SALT).encode()).hexdigest(), "sessions": []})
    return f"Password set for {name}"


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.reply(200, {"status": "ok"})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}"):
            return self.reply(401, {"error": "unauthorized"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)))
        except ValueError:
            return self.reply(400, {"error": "invalid json"})
        name = body.get("name", "")
        if not NAME.fullmatch(name):
            return self.reply(400, {"error": "invalid player name"})
        with LOCK:
            if self.path == "/whitelist":
                return self.reply(200, {"message": whitelist(name, bool(body.get("add")))})
            if self.path == "/password":
                value = body.get("password")
                if value is not None and not isinstance(value, str):
                    return self.reply(400, {"error": "invalid password"})
                return self.reply(200, {"message": password(name, value)})
        self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
