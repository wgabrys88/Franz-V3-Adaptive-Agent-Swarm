import sys
from pathlib import Path
from agent_base import base_parser, fetch_frame, _strip_think, _vlm_post_via_hub

SYSTEM_PROMPT: str = (
    "You are JUDGE, the visual decision authority. "
    "You receive the current canvas image and the full shared memory of all agents. "
    "Agents in memory: WATCHER counts strokes, CRITIC judges completeness, PARSER enforces format. "
    "Your two responsibilities: "
    "1. Look at the image and the shared memory. Reason explicitly: does PARSER have a valid drag? "
    "Does WATCHER agree the stroke makes sense? Does CRITIC agree the drawing is progressing correctly? "
    "2. If all three agents are aligned on the same drag action, output exactly: EXECUTE: drag(x1,y1,x2,y2) "
    "If they are not aligned, output your updated reasoning as plain text ending with drag(x1,y1,x2,y2). "
    "Rules for drag: x1,y1,x2,y2 must be integers 0-1000, x1 != x2 or y1 != y2. "
    "Never output EXECUTE unless you have verified agreement across agents. "
    "If the drawing is fully complete, output exactly: DONE"
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
    result: str = _strip_think(_vlm_post_via_hub(ns.hub_host, ns.hub_port, msgs, 0.1, 150, "JUDGE"))
    sys.stdout.write(result + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
