import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE: Path = Path(__file__).resolve().parent
SECTION_RE: re.Pattern[str] = re.compile(r"^([A-Z][A-Z0-9_]*):\s*(.*)", re.DOTALL | re.MULTILINE)
DRAG_RE: re.Pattern[str] = re.compile(r"drag\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)")
AGENT_TIMEOUT: float = 120.0
JUDGE_FILE: str = "agent_judge.py"


@dataclass(frozen=True)
class RunnerConfig:
    host: str
    port: int
    vlm_url: str
    vlm_model: str
    vlm_timeout: int
    vlm_delay: float
    memory_file: str


_cfg: RunnerConfig | None = None


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _read_sections(path: Path) -> dict[str, str]:
    text: str = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(text))
    for i, m in enumerate(matches):
        name: str = m.group(1)
        start: int = m.end()
        end: int = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        result[name] = text[start:end].strip()
    return result


def _write_section(path: Path, name: str, content: str) -> None:
    text: str = path.read_text(encoding="utf-8")
    pattern: re.Pattern[str] = re.compile(
        r"^" + re.escape(name) + r":.*?(?=^[A-Z][A-Z0-9_]*:|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    replacement: str = f"{name}: {content}\n"
    if pattern.search(text):
        path.write_text(pattern.sub(replacement, text), encoding="utf-8")
    else:
        path.write_text(text.rstrip() + "\n" + replacement, encoding="utf-8")


def _extract_drag(text: str) -> str:
    m = DRAG_RE.search(text)
    return m.group(0) if m else ""


def _agent_cmd(agent_file: str) -> list[str]:
    assert _cfg is not None
    return [
        sys.executable, str(HERE / agent_file),
        "--vlm-url", _cfg.vlm_url,
        "--vlm-model", _cfg.vlm_model,
        "--vlm-timeout", str(_cfg.vlm_timeout),
        "--vlm-delay", str(_cfg.vlm_delay),
        "--hub-host", _cfg.host,
        "--hub-port", str(_cfg.port),
        "--memory-file", _cfg.memory_file,
    ]


def _run_debate(memory_path: Path) -> None:
    agents: list[str] = sorted(p.name for p in HERE.glob("agent_*.py") if p.name != JUDGE_FILE)
    procs: list[tuple[str, subprocess.Popen[bytes]]] = []
    for agent_file in agents:
        name: str = Path(agent_file).stem.upper().replace("AGENT_", "")
        _emit(f"STATUS:{name}|thinking")
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            _agent_cmd(agent_file), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        procs.append((name, proc))
    for name, proc in procs:
        try:
            stdout, _ = proc.communicate(timeout=AGENT_TIMEOUT)
            result: str = stdout.decode("utf-8").strip()
            if result:
                _write_section(memory_path, name, result)
                _emit(f"SWARM:{name}|output|{result}")
        except subprocess.TimeoutExpired:
            proc.kill()
            _emit(f"LOG:warn|{name} timed out")
        finally:
            _emit(f"STATUS:{name}|idle")


def _run_judge(memory_path: Path) -> str | None:
    _emit("STATUS:JUDGE|thinking")
    _emit("CAPTURE:")
    time.sleep(0.5)
    judge_proc: subprocess.Popen[bytes] | None = None
    try:
        judge_proc = subprocess.Popen(
            _agent_cmd(JUDGE_FILE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        j_out, _ = judge_proc.communicate(timeout=AGENT_TIMEOUT)
        judge_result: str = j_out.decode("utf-8").strip()
        if judge_result == "DONE":
            _emit("SWARM:JUDGE|output|DONE")
            return "DONE"
        if judge_result.startswith("EXECUTE:"):
            drag: str = judge_result[8:].strip()
            _emit(f"SWARM:JUDGE|execute|{drag}")
            return drag
        if judge_result:
            _write_section(memory_path, "JUDGE", judge_result)
            _emit(f"SWARM:JUDGE|output|{judge_result}")
    except subprocess.TimeoutExpired:
        if judge_proc is not None:
            judge_proc.kill()
        _emit("LOG:warn|JUDGE timed out")
    finally:
        _emit("STATUS:JUDGE|idle")
    return None


def main() -> None:
    global _cfg

    parser: argparse.ArgumentParser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1234)
    parser.add_argument("--vlm-url", default="http://127.0.0.1:1235/v1/chat/completions")
    parser.add_argument("--vlm-model", default="qwen3.5-0.8b")
    parser.add_argument("--vlm-timeout", type=int, default=120)
    parser.add_argument("--vlm-delay", type=float, default=0.2)
    parser.add_argument("--memory-file", default="")
    parser.add_argument("--capture-width", type=int, default=640)
    parser.add_argument("--capture-height", type=int, default=640)
    parser.add_argument("--action-delay", type=float, default=0.3)
    parser.add_argument("--region", default="")
    ns = parser.parse_args()

    memory_path: Path = Path(ns.memory_file) if ns.memory_file else HERE / "memory.txt"
    if not memory_path.exists():
        _emit("LOG:error|memory.txt not found")
        raise SystemExit(1)

    _cfg = RunnerConfig(
        host=ns.host,
        port=ns.port,
        vlm_url=ns.vlm_url,
        vlm_model=ns.vlm_model,
        vlm_timeout=ns.vlm_timeout,
        vlm_delay=ns.vlm_delay,
        memory_file=str(memory_path),
    )

    agents_found: list[str] = sorted(p.name for p in HERE.glob("agent_*.py"))
    _emit(f"LOG:ok|Runner started. Agents: {', '.join(agents_found)}")
    _emit(f"LOG:ok|Memory: {memory_path}")

    cycle: int = 0
    while True:
        cycle += 1
        _emit(f"LOG:info|Cycle {cycle}")
        _write_section(memory_path, "CYCLE", str(cycle))

        _emit("CAPTURE:")
        time.sleep(0.3)

        _run_debate(memory_path)

        result: str | None = _run_judge(memory_path)

        if result == "DONE":
            _emit("LOG:ok|Task complete.")
            _emit("DONE:")
            break

        if result:
            drag: str = _extract_drag(result)
            if drag:
                _emit(f"LOG:ok|JUDGE approved: {drag}")
                _emit(f"ACTION:{drag}")
                sections: dict[str, str] = _read_sections(memory_path)
                history: str = sections.get("HISTORY", "")
                history_lines: list[str] = [l for l in history.split(" | ") if l]
                history_lines.append(f"cycle={cycle} action={drag}")
                _write_section(memory_path, "HISTORY", " | ".join(history_lines[-5:]))

        time.sleep(0.1)


if __name__ == "__main__":
    main()
