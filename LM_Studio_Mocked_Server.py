import base64
import http.server
import json
import os
import random
import string
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

HOST = "0.0.0.0"
PORT = 1235
IMAGES_DIR = Path("lm_images")
LOG_FILE = Path("lm_studio_log.txt")
TEMPLATES_FILE = Path("mock_templates.json")
DEFAULT_DELAY_MIN = 1.2
DEFAULT_DELAY_MAX = 3.8
MAX_REQUESTS = 200

IMAGES_DIR.mkdir(exist_ok=True)

_requests: deque[dict] = deque(maxlen=MAX_REQUESTS)
_requests_lock = threading.Lock()
_pending_override: dict | None = None
_override_lock = threading.Lock()
_templates_cache: dict = {}
_templates_mtime: float = 0.0
_sse_clients: list = []
_sse_lock = threading.Lock()


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _rand6() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def _load_templates() -> dict:
    global _templates_cache, _templates_mtime
    if not TEMPLATES_FILE.exists():
        return {}
    mtime = TEMPLATES_FILE.stat().st_mtime
    if mtime != _templates_mtime:
        try:
            _templates_cache = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
            _templates_mtime = mtime
        except Exception:
            pass
    return _templates_cache


def _log(text: str) -> None:
    line = f"[{_iso_now()}] {text}\n"
    LOG_FILE.open("a", encoding="utf-8").write(line)
    print(text)


def _sse_broadcast(event: str, data: dict) -> None:
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
    with _sse_lock:
        dead = []
        for wfile in _sse_clients:
            try:
                wfile.write(payload)
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for d in dead:
            _sse_clients.remove(d)


def _save_image(b64_data: str, agent_name: str) -> str:
    filename = f"{_iso_now()}_{agent_name}_{_rand6()}.png"
    path = IMAGES_DIR / filename
    path.write_bytes(base64.b64decode(b64_data))
    return str(path).replace("\\", "/")


def _extract_image(messages: list[dict]) -> tuple[str, str]:
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url: str = part.get("image_url", {}).get("url", "")
                    if url.startswith("data:image/"):
                        b64 = url.split(",", 1)[1] if "," in url else ""
                        return url, b64
    return "", ""


def _pick_template(agent_name: str, templates: dict) -> dict | None:
    key = agent_name.upper()
    if key in templates:
        return templates[key]
    if "DEFAULT" in templates:
        return templates["DEFAULT"]
    return None


def _build_response(content: str, model: str = "mock-qwen3") -> dict:
    return {
        "id": f"chatcmpl-{_rand6()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": len(content.split()), "total_tokens": len(content.split())},
    }


def _handle_completions(body: bytes) -> tuple[int, dict]:
    try:
        req: dict = json.loads(body.decode("utf-8"))
    except Exception:
        return 400, {"error": "bad json"}

    messages: list[dict] = req.get("messages", [])
    temperature: float = float(req.get("temperature", 0.3))
    max_tokens: int = int(req.get("max_tokens", 150))
    agent_name: str = str(req.get("agent_name", "AGENT")).upper()

    raw_url, b64 = _extract_image(messages)
    saved_path = ""
    log_messages = json.dumps(messages, ensure_ascii=True)

    if b64:
        saved_path = _save_image(b64, agent_name)
        log_messages = log_messages.replace(raw_url, f"[SAVED_IMAGE: {saved_path}]")

    _log(f"REQ agent={agent_name} temp={temperature} max_tokens={max_tokens} image={'yes' if b64 else 'no'}")
    _log(f"PAYLOAD {log_messages[:400]}")

    templates = _load_templates()
    tmpl = _pick_template(agent_name, templates)

    with _override_lock:
        global _pending_override
        override = _pending_override
        _pending_override = None

    if override:
        content = override.get("content", "drag(320,240,400,300)")
        delay = 0.0
    elif tmpl:
        responses: list[str] = tmpl.get("responses", ["drag(320,240,400,300)"])
        content = random.choice(responses)
        if "{saved_image_path}" in content:
            content = content.replace("{saved_image_path}", saved_path)
        delay = float(tmpl.get("delay", random.uniform(DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX)))
    else:
        content = "drag(320,240,400,300)"
        delay = random.uniform(DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX)

    req_record: dict = {
        "id": _rand6(),
        "ts": _iso_now(),
        "agent_name": agent_name,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "has_image": bool(b64),
        "saved_image_path": saved_path,
        "messages": messages,
        "response": content,
        "delay": delay,
    }
    with _requests_lock:
        _requests.appendleft(req_record)

    _sse_broadcast("request", {k: v for k, v in req_record.items() if k != "messages"})

    if delay > 0:
        time.sleep(delay)

    _log(f"RESP agent={agent_name} content={content[:120]}")
    return 200, _build_response(content)


class MockHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        match path:
            case "/dashboard/requests":
                with _requests_lock:
                    rows = [
                        {k: v for k, v in r.items() if k != "messages"}
                        for r in _requests
                    ]
                self._send_json(200, {"requests": rows})
            case "/dashboard/request_detail":
                qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                rid = next((p[3:] for p in qs.split("&") if p.startswith("id=")), "")
                with _requests_lock:
                    rec = next((r for r in _requests if r["id"] == rid), None)
                self._send_json(200, rec or {})
            case "/dashboard/templates":
                self._send_json(200, _load_templates())
            case "/dashboard/log":
                lines = LOG_FILE.read_text(encoding="utf-8").splitlines() if LOG_FILE.exists() else []
                self._send_json(200, {"lines": lines[-200:]})
            case "/dashboard/events":
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                try:
                    self.wfile.write(b"event: connected\ndata: {}\n\n")
                    self.wfile.flush()
                    with _sse_lock:
                        _sse_clients.append(self.wfile)
                    while True:
                        time.sleep(30)
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except Exception:
                    with _sse_lock:
                        if self.wfile in _sse_clients:
                            _sse_clients.remove(self.wfile)
            case _:
                self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        match path:
            case "/v1/chat/completions" | "/v1/completions":
                code, resp = _handle_completions(body)
                self._send_json(code, resp)
            case "/dashboard/templates":
                try:
                    data = json.loads(body.decode("utf-8"))
                    TEMPLATES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
                    global _templates_mtime
                    _templates_mtime = 0.0
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
            case "/dashboard/override":
                try:
                    data = json.loads(body.decode("utf-8"))
                    with _override_lock:
                        global _pending_override
                        _pending_override = data
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
            case _:
                self._send_json(404, {"error": "not found"})


def main() -> None:
    server = http.server.ThreadingHTTPServer((HOST, PORT), MockHandler)
    print(f"LM Studio Mock Server running on http://{HOST}:{PORT}")
    print(f"Dashboard API at http://localhost:{PORT}/dashboard/")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
