
class HubHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format_str: str, *args: object) -> None:
        pass

    def _send_json(self, code: int, data: dict) -> None:
        body: bytes = json.dumps(data, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send_file(self, code: int, content: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _handle_sse(self) -> None:
        if _panel_connected is not None and _loop is not None:
            _loop.call_soon_threadsafe(_panel_connected.set)
        sub: dict = _bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"event: connected\ndata: {}\n\n")
            self.wfile.flush()
            while sub["active"]:
                try:
                    payload: dict | None = sub["queue"].get(timeout=25)
                except Exception:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        break
                    continue
                if payload is None:
                    break
                evt: str = payload.get("event", "message")
                dat: str = json.dumps(payload.get("data", {}), ensure_ascii=True)
                chunk: bytes = f"event: {evt}\ndata: {dat}\n\n".encode("utf-8")
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    break
        finally:
            _bus.unsubscribe(sub)

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        match path:
            case "/" | "/index.html":
                self._send_file(200, PANEL_PATH.read_bytes(), "text/html; charset=utf-8")
            case "/events":
                self._handle_sse()
            case "/state":
                if _panel_connected is not None and _loop is not None:
                    _loop.call_soon_threadsafe(_panel_connected.set)
                self._send_json(200, _build_state_snapshot())
            case "/frame":
                self._send_json(200, {"seq": _ann_pending_seq, "raw_b64": _raw_b64_for_panel, "overlays": _overlays_for_panel})
            case "/config":
                self._send_json(200, dataclasses.asdict(_cfg))
            case "/swarm":
                after: int = 0
                qs: str = self.path.split("?", 1)[1] if "?" in self.path else ""
                for param in qs.split("&"):
                    if param.startswith("after="):
                        try:
                            after = int(param[6:])
                        except ValueError:
                            pass
                with _swarm_lock:
                    msgs: list[dict] = list(_swarm_messages[after:])
                    total: int = len(_swarm_messages)
                self._send_json(200, {
                    "messages": [{"agent": m.get("agent", ""), "direction": m.get("direction", ""), "text": m.get("text", ""), "has_image": bool(m.get("image_b64", "")), "ts": m.get("ts", 0)} for m in msgs],
                    "total": total,
                })
            case _ if path.startswith("/swarm_image/"):
                try:
                    idx: int = int(path.split("/")[2])
                except (IndexError, ValueError):
                    self._send_json(404, {"error": "bad index"})
                    return
                with _swarm_lock:
                    img_b64: str = _swarm_messages[idx].get("image_b64", "") if 0 <= idx < len(_swarm_messages) else ""
                if img_b64:
                    self._send_file(200, base64.b64decode(img_b64), "image/png")
                else:
                    self._send_json(404, {"error": "no image"})
            case "/event_log":
                with _event_log_lock:
                    entries: list[dict] = list(_event_log)
                self._send_json(200, {"entries": entries})
            case _:
                self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path: str = self.path.split("?", 1)[0]
        content_length: int = int(self.headers.get("Content-Length", "0"))
        body: bytes = self.rfile.read(content_length) if content_length > 0 else b""
        match path:
            case "/annotated":
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                if not isinstance(parsed, dict):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                seq_val = parsed.get("seq")
                img_val = parsed.get("image_b64", "")
                if seq_val != _ann_pending_seq:
                    self._send_json(409, {"ok": False, "err": f"seq mismatch got={seq_val} want={_ann_pending_seq}"})
                    return
                if not isinstance(img_val, str) or len(img_val) < ANN_MIN_LEN:
                    self._send_json(400, {"ok": False, "err": "image too short"})
                    return
                global _ann_result_b64
                _ann_result_b64 = img_val
                if _ann_ready is not None and _loop is not None:
                    _loop.call_soon_threadsafe(_ann_ready.set)
                self._send_json(200, {"ok": True, "seq": seq_val})
            case "/vlm":
                try:
                    req_data = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"error": "bad json"})
                    return
                messages: list[dict] = req_data.get("messages", [])
                temperature: float = float(req_data.get("temperature", 0.3))
                max_tokens: int = int(req_data.get("max_tokens", 150))
                agent_name: str = str(req_data.get("agent_name", "AGENT"))
                if _loop is None:
                    self._send_json(503, {"error": "hub not ready"})
                    return
                fut = asyncio.run_coroutine_threadsafe(
                    _vlm_proxy_call(messages, temperature, max_tokens, agent_name), _loop
                )
                try:
                    result: str = fut.result(timeout=_cfg.vlm_timeout_seconds + 10)
                    swarm_message(agent_name, "output", result)
                    self._send_json(200, {"content": result})
                except Exception as exc:
                    log_event(f"VLM proxy error: {exc}", "error")
                    self._send_json(500, {"error": str(exc)})
            case _:
                self._send_json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

async def _pipe_reader(proc: asyncio.subprocess.Process, memory_path: Path) -> None:
    assert proc.stdout is not None
    while True:
        try:
            raw: bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=300.0)
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                break
            continue
        if not raw:
            break
        line_str: str = raw.decode("utf-8", errors="replace").strip()
        if not line_str:
            continue
        _log_to_disk(f"[PIPE] {line_str}")
        if line_str.startswith("ACTION:"):
            action_dict = _route_action_string(line_str[7:].strip())
            if action_dict and "_special" not in action_dict:
                enqueue_action(action_dict)
        elif line_str.startswith("SWARM:"):
            parts: list[str] = line_str[6:].split("|", 3)
            if len(parts) >= 3:
                swarm_message(parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else "")
        elif line_str.startswith("STATUS:"):
            sp: list[str] = line_str[7:].split("|", 1)
            if len(sp) == 2:
                set_agent_status(sp[0], sp[1])
        elif line_str.startswith("LOG:"):
            sp2: list[str] = line_str[4:].split("|", 1)
            if len(sp2) == 2:
                log_event(sp2[1], sp2[0])
            else:
                log_event(line_str[4:])
        elif line_str.startswith("OVERLAY:"):
            try:
                add_overlay(json.loads(line_str[8:]))
            except (json.JSONDecodeError, ValueError):
                pass
        elif line_str.startswith("CAPTURE:"):
            asyncio.create_task(_do_capture_cycle())
        elif line_str.startswith("SET_CONFIG:"):
            try:
                patch: dict = json.loads(line_str[11:])
                global _cfg
                _cfg = dataclasses.replace(_cfg, **{k: v for k, v in patch.items() if hasattr(_cfg, k)})
                log_event(f"Config patched: {patch}")
            except Exception as exc:
                log_event(f"SET_CONFIG error: {exc}", "error")
        elif line_str.startswith("DONE:"):
            log_event("Runner signalled DONE", "ok")
    rc: int | None = proc.returncode
    log_event(f"Runner pipe closed (rc={rc})", "warn" if rc else "info")


def _build_runner_cmd(memory_path: Path) -> list[str]:
    return [
        sys.executable, str(HERE / "runner.py"),
        "--host", _cfg.server_host,
        "--port", str(_cfg.server_port),
        "--vlm-url", _cfg.vlm_endpoint_url,
        "--vlm-model", _cfg.vlm_model_name,
        "--vlm-timeout", str(_cfg.vlm_timeout_seconds),
        "--vlm-delay", str(_cfg.vlm_request_delay_seconds),
        "--capture-width", str(_cfg.capture_width),
        "--capture-height", str(_cfg.capture_height),
        "--action-delay", str(_cfg.action_delay_seconds),
        "--memory-file", str(memory_path),
        *(["--region", _cfg.capture_region] if _cfg.capture_region else []),
    ]


async def _async_main() -> None:
    global _action_queue, _ann_ready, _panel_connected, _capture_requested
    global _vlm_orchestrator_sem, _vlm_agent_sem

    _action_queue = asyncio.Queue()
    _ann_ready = asyncio.Event()
    _panel_connected = asyncio.Event()
    _capture_requested = asyncio.Event()

    _vlm_orchestrator_sem = asyncio.Semaphore(_cfg.max_orchestrator_vlm_concurrent)
    _vlm_agent_sem = asyncio.Semaphore(_cfg.max_agent_vlm_concurrent)

    asyncio.create_task(_action_executor_loop())
    asyncio.create_task(_capture_loop())

    log_event("Waiting for panel connection...")
    _bus.publish("state", _build_state_snapshot())
    while not _panel_connected.is_set():
        await asyncio.sleep(0.2)
    log_event("Panel connected", "ok")
    await asyncio.sleep(0.5)

    memory_path: Path
    if _session_dir is not None:
        memory_path = _session_dir / "memory.txt"
        if MEMORY_SRC.exists():
            shutil.copy(MEMORY_SRC, memory_path)
        elif not memory_path.exists():
            memory_path.write_text("GOAL: Draw a cat.\nWATCHER:\nCRITIC:\nPARSER:\nJUDGE:\nCYCLE: 0\nHISTORY:\n", encoding="utf-8")
    else:
        memory_path = MEMORY_SRC if MEMORY_SRC.exists() else HERE / "memory.txt"

    log_event(f"Launching runner with memory: {memory_path}")

    while True:
        proc: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *_build_runner_cmd(memory_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log_event(f"Runner PID {proc.pid}", "ok")
        try:
            await _pipe_reader(proc, memory_path)
        except asyncio.CancelledError:
            proc.terminate()
            raise
        except Exception as exc:
            log_event(f"Pipe reader error: {exc}", "error")
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        log_event("Runner exited. Restarting in 3s...", "warn")
        await asyncio.sleep(3.0)


def _run_select_region() -> tuple[str, int]:
    proc: subprocess.CompletedProcess[bytes] = subprocess.run(
        [sys.executable, str(WIN32_PATH), "select_region"], capture_output=True,
    )
    if proc.returncode == 2:
        return "", 2
    if proc.returncode != 0 or not proc.stdout:
        return "", proc.returncode
    return proc.stdout.decode("ascii").strip(), 0


def main() -> None:
    global _cfg, _loop

    args: list[str] = sys.argv[1:]
    skip_region: bool = False
    idx: int = 0
    while idx < len(args):
        if args[idx] == "--skip-region":
            skip_region = True
        idx += 1

    capture_region: str = _cfg.capture_region
    if not skip_region:
        print("Select capture region (drag), right-click for full screen, Escape to quit.")
        region_str, exit_code = _run_select_region()
        if exit_code == 2:
            print("Cancelled.")
            raise SystemExit(0)
        capture_region = region_str
        print(f"Region: {capture_region}" if capture_region else "Full screen mode.")
        _cfg = dataclasses.replace(_cfg, capture_region=capture_region)

    _init_session()

    host: str = _cfg.server_host
    port: int = _cfg.server_port
    print(f"\nFranz Hub V3 starting on http://{host}:{port}")
    print(f"VLM: {_cfg.vlm_endpoint_url}")
    print(f"Region: {_cfg.capture_region or 'full screen'}")
    if _session_dir:
        print(f"Session: {_session_dir}")
    print(f"\nOpen http://{host}:{port} in Chrome to start.\n")

    server: http.server.ThreadingHTTPServer = http.server.ThreadingHTTPServer((host, port), HubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"HTTP server running at http://{host}:{port}")

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_async_main())
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()
        _loop.close()
        print("Franz Hub V3 stopped.")


if __name__ == "__main__":
    main()
