import sys
from pathlib import Path
from agent_base import base_parser, fetch_frame, _strip_think, _vlm_post_via_hub

SYSTEM_PROMPT: str = (
    "You are WATCHER, a stroke-counting specialist. "
    "You receive the full shared memory of all agents. "
    "Your job: count visible strokes, name visible body parts of the shape being drawn, then propose the single most important next drag action. "
    "Format your response as plain text ending with drag(x1,y1,x2,y2) using integer coordinates 0-1000."
)


def main() -> None:
    p = base_parser()
    ns = p.parse_args()
    memory: str = Path(ns.memory_file).read_text(encoding="utf-8")
    frame_b64: str = fetch_frame(ns.hub_host, ns.hub_port)
    if frame_b64:
        user_content: list[dict] = [
            {"type": "text", "text": memory},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{frame_b64}"}},
        ]
    else:
        user_content = [{"type": "text", "text": memory}]
    msgs: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result: str = _strip_think(_vlm_post_via_hub(ns.hub_host, ns.hub_port, msgs, 0.3, 120, "WATCHER"))
    sys.stdout.write(result + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
