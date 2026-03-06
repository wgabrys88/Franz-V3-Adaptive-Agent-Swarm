import argparse
import json
import sys
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    vlm_url: str
    vlm_model: str
    vlm_timeout: int
    vlm_delay: float
    hub_host: str
    hub_port: int
    memory_file: str


def base_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--vlm-url", default="http://127.0.0.1:1235/v1/chat/completions")
    p.add_argument("--vlm-model", default="qwen3.5-0.8b")
    p.add_argument("--vlm-timeout", type=int, default=120)
    p.add_argument("--vlm-delay", type=float, default=0.2)
    p.add_argument("--hub-host", default="127.0.0.1")
    p.add_argument("--hub-port", type=int, default=1234)
    p.add_argument("--memory-file", required=True)
    return p


def fetch_frame(host: str, port: int) -> str:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/frame", timeout=10) as resp:
            data: dict = json.loads(resp.read().decode("utf-8"))
        return str(data.get("raw_b64", ""))
    except Exception:
        return ""


def _strip_think(raw: str) -> str:
    cleaned: str = raw
    while "<think>" in cleaned and "</think>" in cleaned:
        s: int = cleaned.find("<think>")
        e: int = cleaned.find("</think>") + len("</think>")
        cleaned = cleaned[:s] + cleaned[e:]
    return cleaned.strip()


def _vlm_post_via_hub(
    hub_host: str,
    hub_port: int,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    agent_name: str,
) -> str:
    body: bytes = json.dumps({
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "agent_name": agent_name,
    }, ensure_ascii=True).encode("utf-8")
    req = urllib.request.Request(
        f"http://{hub_host}:{hub_port}/vlm",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data: dict = json.loads(resp.read().decode("utf-8"))
        return str(data.get("content", ""))
    except Exception as exc:
        sys.stderr.write(f"VLM proxy error: {exc}\n")
        return ""
