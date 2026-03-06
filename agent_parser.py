import sys
from pathlib import Path
from agent_base import base_parser, _strip_think, _vlm_post_via_hub

SYSTEM_PROMPT: str = (
    "You are PARSER, an action format specialist. "
    "You receive the full shared memory of all agents. "
    "Your job: read all agent sections, find the drag action with the most agreement, validate its format, and output the single correct action. "
    "You must output ONLY: drag(x1,y1,x2,y2) where x1,y1,x2,y2 are integers 0-1000. "
    "No other text. No explanation. Just the drag action on one line. "
    "If no valid drag action exists in any section, output: drag(400,300,600,300)"
)


def main() -> None:
    p = base_parser()
    ns = p.parse_args()
    memory: str = Path(ns.memory_file).read_text(encoding="utf-8")
    msgs: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": memory},
    ]
    result: str = _strip_think(_vlm_post_via_hub(ns.hub_host, ns.hub_port, msgs, 0.0, 40, "PARSER"))
    sys.stdout.write(result + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
