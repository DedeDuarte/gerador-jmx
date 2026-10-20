"""Servidor local que imita a aplicação gravada em fixtures/mini_login.har.

Cada resposta gera valores novos (csrf, token, ids), então o JMX só passa se as
correlações estiverem certas. Uso: `python tests/mock_server.py [porta]`.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = 18765
USER, PASSWORD = "ana@teste.com", "Senha@123"


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, str] = {}      # sid -> csrf
        self.tokens: dict[str, str] = {}        # token -> user id
        self.orders: set[str] = set()


STATE = State()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: str, ctype="application/json", headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj))

    def _cookie(self, name: str) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def _user(self) -> str | None:
        auth = self.headers.get("Authorization") or ""
        token = auth.removeprefix("Bearer ").strip()
        return STATE.tokens.get(token)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        url = urlsplit(self.path)
        parts = url.path.strip("/").split("/")
        if url.path == "/login":
            sid, csrf = secrets.token_hex(8), "csrf" + secrets.token_hex(6)
            with STATE.lock:
                STATE.sessions[sid] = csrf
            html = (f'<html><body><form method="post" action="/api/login">'
                    f'<input type="hidden" name="csrf_token" value="{csrf}">'
                    f'</form></body></html>')
            return self._send(200, html, "text/html; charset=utf-8",
                              [("Set-Cookie", f"sid={sid}; Path=/; HttpOnly")])
        if len(parts) == 4 and parts[:2] == ["api", "users"] and parts[3] == "orders":
            if self._user() != parts[2]:
                return self._json(401, {"erro": "token ou usuário inválido"})
            if parse_qs(url.query).get("page") != ["1"]:
                return self._json(400, {"erro": "page"})
            order = f"ORD-{secrets.randbelow(10**8):08d}"
            with STATE.lock:
                STATE.orders.add(order)
            return self._json(200, {"orders": [{"orderId": order, "total": 99.9}]})
        self._json(404, {"erro": "não encontrado"})

    def do_POST(self):
        parts = urlsplit(self.path).path.strip("/").split("/")
        body = self._body()
        if parts == ["api", "login"]:
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            sid = self._cookie("sid")
            if not sid or STATE.sessions.get(sid) != form.get("csrf_token"):
                return self._json(403, {"erro": "csrf"})
            if form.get("username") != USER or form.get("password") != PASSWORD:
                return self._json(401, {"erro": "credenciais"})
            token, uid = "tok_" + secrets.token_hex(8), str(4_000_000 + secrets.randbelow(999_999))
            with STATE.lock:
                STATE.tokens[token] = uid
            return self._json(200, {"access_token": token, "user": {"id": int(uid), "name": "Ana"}})
        if len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "confirm":
            data = json.loads(body or b"{}")
            if not self._user():
                return self._json(401, {"erro": "token"})
            if parts[2] not in STATE.orders or data.get("orderId") != parts[2]:
                return self._json(404, {"erro": "pedido"})
            if self.headers.get("x-app-version") != "3.2.1":
                return self._json(400, {"erro": "versão"})
            return self._json(200, {"status": "ok"})
        self._json(404, {"erro": "não encontrado"})


def start(port: int = PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else PORT), Handler)
    print(f"mock em http://localhost:{srv.server_port}")
    srv.serve_forever()
