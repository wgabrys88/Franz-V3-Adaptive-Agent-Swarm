- Python 3.13 only. Modern syntax, strict typing with dataclasses and pattern matching. No legacy code, no compatibility shims.

- Windows 11 only. No cross-platform fallbacks or compatibility layers.

- Latest Google Chrome browser support only.

- OpenAI API Stateless /chat/completions is used by design (via VLM proxy).

- Maximum code reduction. Remove every possible line while keeping 100% original functionality.

- Perfect Pylance/pyright compatibility, full type hints, frozen dataclasses for all config.

- No comments anywhere in any file. Code blocks must contain no non-ASCII characters.

- No slicing or truncating of data anywhere in the code.

- No functional "magic values" in the code — all constants must live in frozen dataclasses.

- Files .html must use latest HTML5 + modern CSS (custom properties, grid/flex) + modern JS (ES2024). All HTML files must follow the exact dark cyber aesthetic of panel.html and execution_replay.html.

- Use native Qwen3 / Qwen3.5 VL input and output format when prompting.

- Use knowledge about the VLM model training data for prompt correction. Ensure small 0.8B–2B versions of Qwen3.5 VL will be properly prompted (short, explicit, format-enforcing, minimal CoT).

- Ensure the code does not contain any duplications and dead functionalities. Remove any functional fallbacks.

- The architecture must always follow the v3 design: separate runner subprocess with async stdout pipe IPC (no file polling), VLM proxy endpoint in franz_hub.py (POST /vlm with separate semaphores), shared agent_base.py for all agents, continuous runner with JUDGE approval gate before any ACTION, unified _do_capture_cycle, and every agent must fetch the screen image via fetch_frame.

- All agents must import from agent_base.py. No duplicated _vlm_post, _strip_think, or fetch_frame logic is allowed anywhere.

- Always maintain and update two companion tools when the architecture changes: execution_replay.html (must parse real events.txt and animate the exact V3 data flows) and LM_Studio_Mocked_Server.py + LM_Studio_Mocked_Server.html (with configurable mock_templates.json, image saving to lm_images/, and live SSE dashboard).

- The entire framework must remain completely generic and adaptive: agent names and roles are discovered dynamically from runner.py and memory.txt. No hard-coded agent lists or assumptions about tasks (drawing, coding, research, etc.).

- All prompts sent to the small Qwen3.5 0.8B VL model must be optimized for its training data: explicit format enforcement, minimal chain-of-thought, and _strip_think() must be applied to every response.

- Session logging is mandatory and non-negotiable: timestamped events.txt + PNG frames in logs/<session>/ directory. No data truncation allowed in logs.

- Config must always use frozen dataclasses (immutable after startup, passed via CLI). Runtime changes are only allowed via SET_CONFIG: IPC line.

- When generating new agents or modifying prompts, the output must always end with the exact required format (drag(x1,y1,x2,y2), EXECUTE:..., or DONE). No extra text is allowed unless the agent role explicitly requires it.

- The system must be fully reproducible: every run must be debuggable with execution_replay.html and the LM Studio mock dashboard without any external dependencies.
