

```markdown
# Franz-AI — Swarm-Driven GUI Automation Framework

> **Python orchestrates I/O. LLM agents orchestrate intelligence.**

Franz-AI is a modular automation framework where a swappable **brain** module drives
any Windows GUI application through a tight **capture → annotate → observe → decide → act** loop.
The framework (plumbing) is deterministic infrastructure. The brain is the replaceable
policy layer where all task-specific cognition lives — powered by LLM agent swarms.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRANZ ENGINE LOOP                        │
│                                                                 │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│   │ CAPTURE  │───>│ ANNOTATE │───>│ VLM CALL │───>│  BRAIN   │ │
│   │ win32.py │    │ panel.html│    │ router.py│    │ brain.py │ │
│   └──────────┘    └──────────┘    └──────────┘    └────┬─────┘ │
│        ^                                               │       │
│        │           ┌──────────┐                        │       │
│        └───────────│ EXECUTE  │<───────────────────────┘       │
│                    │ win32.py │                                 │
│                    └──────────┘                                 │
└─────────────────────────────────────────────────────────────────┘

         ┌─────────────────────────────────────────┐
         │           BROWSER PANEL                  │
         │                                          │
         │  ┌────────────┐  ┌───────────────────┐  │
         │  │ Canvas     │  │ Swarm Wire        │  │
         │  │ (annotated │  │ (agent messages)  │  │
         │  │  view)     │  │                   │  │
         │  └────────────┘  ├───────────────────┤  │
         │                  │ Event Log         │  │
         │                  │ (system events)   │  │
         │                  └───────────────────┘  │
         └─────────────────────────────────────────┘
```

### Data Flow — One Turn

```
1. win32.py  ──capture──>  raw screenshot (PNG bytes)
2. router.py  sends raw PNG + overlays to panel.html via HTTP
3. panel.html  renders overlays on canvas, composites, POSTs back annotated PNG
4. router.py  sends annotated PNG + user_text to VLM endpoint (OpenAI-compatible API)
5. VLM returns observation text
6. router.py  calls brain.on_vlm_response(observation_text)
7. brain runs agent swarm (may execute actions mid-swarm, re-capture, re-observe)
8. brain returns next user_text string
9. router.py  drains action queue, executes via win32.py subprocesses
10. router.py  drains overlay queue for next frame
11. Loop back to step 1
```

### Mid-Swarm Execution

The brain can execute actions (drags, clicks, keystrokes) **during** its
`on_vlm_response` call, before returning. This closes the perception-action
loop within a single turn:

```
brain.on_vlm_response(text):
    agent_1 decides what to draw
    agent_2 produces drag commands
    ──> execute drags on screen NOW
    ──> capture fresh screenshot
    agent_3 verifies the result
    return next_prompt
```

Later agents in the swarm see the **updated** world state, not the stale
screenshot from the start of the turn. This prevents long-horizon drift.

---

## File Structure

| File | Role | Changes? |
|------|------|----------|
| `franz.py` | Entry point, queues, action/overlay helpers, brain loader | Never |
| `router.py` | Engine loop, HTTP server, VLM calls, session logging | Never |
| `win32.py` | Pure ctypes Win32 capture, input simulation, region selector | Never |
| `panel.html` | Browser UI: annotation renderer, swarm wire, event log | Never |
| `brain*.py` | **Swappable brain** — all task intelligence lives here | Always |

The first four files are **stable plumbing**. You only ever create or modify brain files.

---

## Normalized Coordinate System

All coordinates use a **[0, 1000] normalized space**:

- `(0, 0)` = top-left corner of the capture region
- `(1000, 1000)` = bottom-right corner
- Resolution-independent: same coordinates work on any screen size
- The `--region` flag maps coordinates to the selected screen area

---

## Brain Contract

Every brain must implement exactly one function:

```python
def on_vlm_response(text: str) -> str:
    """
    Input:  VLM's text description of the current annotated screenshot.
    Output: The next user_text string that becomes the next VLM call's prompt.

    Side effects (optional):
    - franz.actions(action_dict)   — queue clicks, drags, keystrokes
    - franz.overlays(overlay_dict) — queue visual overlays for next frame
    - franz.swarm_message(...)     — log agent messages to swarm wire
    - Direct subprocess calls to win32.py for mid-swarm execution
    """
```

### Available Action Helpers

```python
import franz

franz.actions(franz.click(x, y))
franz.actions(franz.double_click(x, y))
franz.actions(franz.right_click(x, y))
franz.actions(franz.type_text("hello"))
franz.actions(franz.press_key("enter"))
franz.actions(franz.hotkey("ctrl+s"))
franz.actions(franz.scroll_up(x, y))
franz.actions(franz.scroll_down(x, y))
franz.actions(franz.drag_start(x, y))
franz.actions(franz.drag_end(x, y))
```

### Available Overlay Helpers

```python
franz.overlays(franz.dot(x, y, label="point", color="#00ff00"))
franz.overlays(franz.box(x1, y1, x2, y2, label="region", stroke_color="#ff6600"))
franz.overlays(franz.line([[x1,y1],[x2,y2]], label="stroke", color="#4488ff"))
```

### Custom Overlay Format

Overlays support full polygon rendering with labels, fills, strokes, dashes,
opacity, and glow effects:

```python
franz.overlays({
    "points": [[x1,y1], [x2,y2], [x3,y3]],
    "closed": True,            # Close the polygon path
    "stroke": "#ff6600",       # Stroke color
    "fill": "rgba(255,0,0,0.1)", # Fill color
    "stroke_width": 2,
    "dash": [6, 4],            # Dash pattern
    "opacity": 0.8,
    "glow": True,              # Shadow glow effect
    "label": "Triangle",
    "label_position": [x, y],  # Normalized coordinates
    "label_style": {
        "font_size": 10,
        "bg": "#000000",       # Background rectangle
        "color": "#ffffff",
        "align": "left",       # left, center, right
    },
})
```

---

## Configuration

Brains expose configuration as module-level variables. The engine reads them
via `franz.cfg(brain, "NAME", default, cast)`:

```python
# VLM endpoint
VLM_ENDPOINT_URL: str = "http://127.0.0.1:1235/v1/chat/completions"
VLM_MODEL_NAME: str = "your-model-name"
VLM_TEMPERATURE: float = 0.3
VLM_TOP_P: float = 0.8
VLM_MAX_TOKENS: int = 350
VLM_TIMEOUT: int = 360
VLM_REQUEST_DELAY: float = 1.0  # Seconds to wait before each VLM API call

# Server
SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 1234

# Capture
CAPTURE_REGION: str = ""       # Empty = full screen; "x1,y1,x2,y2" normalized
CAPTURE_WIDTH: int = 640
CAPTURE_HEIGHT: int = 640
CAPTURE_DELAY_SECONDS: float = 1.5
ACTION_DELAY_SECONDS: float = 0.1

# Cursor
SHOW_CURSOR: bool = True
CURSOR_COLOR: str = "#ff4444"
CURSOR_ARM: int = 14

# System prompt for the engine's VISIONARY VLM call
SYSTEM_PROMPT: str = "..."
```

---

## Quick Start

### Prerequisites

- Windows 10/11
- Python 3.12+
- A VLM server running an OpenAI-compatible API (e.g., LM Studio, vLLM, Ollama)
- A web browser (Chrome recommended)

### Running

```bash
python franz.py
```

1. Select a brain from the menu (or auto-selects if only one `brain*.py` exists)
2. Draw a capture region (drag), right-click for full screen, Escape to cancel
3. Open `http://127.0.0.1:1234` in Chrome
4. The engine starts automatically when the panel connects

### LM Studio Configuration

If using LM Studio as your VLM server:

- **Disable** "Offload KV Cache to GPU" to prevent VRAM exhaustion crashes
- Set the server port to match your brain's `VLM_ENDPOINT_URL`
- Use a vision-capable model (e.g., Qwen2-VL, LLaVA)

---

## Panel Features

The browser panel at `http://127.0.0.1:1234` provides:

- **Annotated View** — Live canvas showing the captured screenshot with overlays
- **Swarm Wire** — Real-time feed of all agent messages (expandable, with images)
- **Event Log** — System events (annotations, SSE status, errors)
- **Status Bar** — Current phase, turn number, sequence number
- **Image Size Slider** — Adjusts presentation size of images in swarm wire
- **MIN/ALL Toggle** — Collapse/expand all swarm message details
- **SAVE Button** — Downloads complete panel state as a self-contained HTML file
  (all images inlined as data URIs at full quality)

### SSE with Polling Fallback

The panel connects via Server-Sent Events for real-time push updates. If SSE
disconnects, it automatically falls back to polling at 2-second intervals.

---

## Session Logging

Every run creates a timestamped session directory under `logs/`:

```
logs/
  20260303_121958_817073/
    turns.txt          # Full turn log with inputs, outputs, swarm messages
    20260303_121958.png # Annotated screenshots (one per turn)
    20260303_122011.png
    ...
```

### turns.txt Format

```
[TURN 1] [20260303_121958_817073] [INPUT]
(the user_text sent to VLM)

[TURN 1] [20260303_122011_017961] [OUTPUT]
(the VLM's response)

[TURN 1] [20260303_122011_023216] [SWARM] (2 messages)
    >> VISIONARY [+IMG] [12:19:58.818]
       [SYS] system prompt here
       user message here
    << VISIONARY [12:20:11.017]
       response here
```

---

## Example Brain: Star of David Benchmark

The included `brain_bench.py` demonstrates the framework by drawing a Star of
David (hexagram) pattern in MS Paint using a single CONDUCTOR agent:

```
TARGET PATTERN:
  Upward triangle:  (500,100) -> (150,700) -> (850,700) -> (500,100)
  Downward triangle: (500,900) -> (850,300) -> (150,300) -> (500,900)
  Center mark: small cross at (500,500)
```

### How It Works

1. The engine's VISIONARY observes the canvas and describes what it sees
2. The brain passes the observation to CONDUCTOR with the target pattern
3. CONDUCTOR outputs `drag(x1,y1,x2,y2)` commands for the next strokes
4. The brain executes drags mid-swarm via win32.py
5. If 3+ strokes were drawn, a fresh capture is taken and CONDUCTOR verifies
6. Visual overlays show progress: green=done, orange=remaining, blue=reference

### Overlay System

The benchmark renders three overlay layers so both the VLM and the user can
track progress:

- **Green** solid lines with `[OK]` labels for completed strokes
- **Orange** lines with stroke names for remaining strokes
- **Blue** dashed faint lines showing the full target pattern
- **Progress counter** in the top-left corner (e.g., "6/8 strokes (75%)")

---

## Advanced Benchmark: Creative Star of David

The `brain_bench_creative.py` brain tests whether the LLM agents can
**deduce geometry from a natural language goal** rather than following
explicit coordinate instructions.

### Architecture

```
VISIONARY (engine)          PLANNER                    EXECUTOR
    |                          |                          |
    | "I see 3 black lines     |                          |
    |  forming a triangle..."  |                          |
    |------------------------->|                          |
    |                          | "Draw the second         |
    |                          |  triangle: line from     |
    |                          |  (500,900) to (850,300)" |
    |                          |------------------------->|
    |                          |                          |
    |                          |               drag(500,900,850,300)
    |                          |               drag(850,300,150,300)
    |                          |               drag(150,300,500,900)
```

- **PLANNER** receives the geometric concept ("two overlapping equilateral
  triangles centered at 500,500") but NOT exact coordinates. It must reason
  about where to place vertices.
- **EXECUTOR** converts PLANNER's natural language stroke descriptions into
  `drag()` commands.
- Over multiple turns, the VISIONARY reports what was actually drawn, allowing
  the PLANNER to course-correct.

### Key Difference from Benchmark

| Aspect | Benchmark (`brain_bench.py`) | Creative (`brain_bench_creative.py`) |
|--------|------|---------|
| Target coordinates | Hardcoded in CONDUCTOR prompt | Agent must deduce from geometry |
| Agent count | 1 (CONDUCTOR) | 2 (PLANNER + EXECUTOR) |
| Completion detection | Coordinate matching with tolerance | Turn limit (20 turns) |
| Intelligence location | In the prompt (lookup task) | In the model (reasoning task) |

---

## Investigation Guide

When debugging a brain or analyzing a run, follow this systematic process:

### Step 1: Check CLI Output

The terminal shows the high-level flow:

```
Turn N: X actions (Y drags, Z other)
  AGENT_NAME: response preview...
    [OK] Matched stroke: label
    [??] Unmatched drag: (x1,y1,x2,y2)
```

Look for: empty responses, error messages, unmatched drags, unexpected agent output.

### Step 2: Read turns.txt

The session log in `logs/<session>/turns.txt` contains every detail:

- **INPUT sections**: What text was sent to the VLM. Check for:
  - Is the prompt appropriate for the current state?
  - Is it the same text repeating (stuck in a loop)?
  - Is it too long (context overflow risk)?

- **OUTPUT sections**: What the VLM returned. Check for:
  - Empty outputs (VLM failure)
  - Hallucinations (VLM says things that are not on screen)
  - Overly long rambling responses

- **SWARM sections**: All agent messages with timestamps. Check for:
  - Agent call sequence (who was called in what order)
  - What each agent actually received (`>>`) vs produced (`<<`)
  - Error messages (`!!`)
  - System prompts (`[SYS]`) — verify they match expectations

### Step 3: Check VLM Server Logs

If using LM Studio, the server log shows:

- **Request format**: Verify correct OpenAI-compatible structure
- **Token counts**: `prompt_tokens` and `completion_tokens` in response
- **Slot management**: `n_ctx_slot` vs `task.n_tokens` — is the context overflowing?
- **Prompt cache**: Watch for growing cache entries that exhaust memory
- **Timing**: `prompt eval time` and `eval time` for performance analysis
- **Errors**: HTTP 400, channel errors, model crashes

### Step 4: Check Panel Saved Logs

Click SAVE in the panel to export the complete state as HTML. This captures:

- All swarm messages with full text and images
- The last annotated canvas frame
- The event log with timestamps

### Step 5: Compare Screenshots

The `logs/<session>/*.png` files are annotated screenshots from each turn.
Open them in sequence to see what the VLM was actually observing. Compare
the visual state to what the VLM reported in the OUTPUT sections.

### Common Issues and Solutions

| Symptom | Likely Cause | Investigation |
|---------|-------------|---------------|
| Empty VLM responses | Server crash or overload | Check server logs for errors |
| Same input repeating every turn | Brain returns static text | Check brain's `on_vlm_response` return value |
| Agent calls same text every time | Prompt too similar to examples | Check system prompt for ambiguous examples |
| Context overflow (HTTP 400) | Too many tokens | Check `n_ctx_slot` vs `task.n_tokens` in server log |
| Drags at wrong coordinates | VLM gave approximate coords | Check if PLANNER used VLM coords instead of canonical |
| Server crash after many turns | VRAM exhaustion from KV cache | Disable "Offload KV Cache to GPU" in LM Studio |
| Overlays not visible | Wrong coordinate space | Verify overlay coords are in [0, 1000] normalized space |

---

## Design Principles

1. **The engine is deterministic plumbing** — capture, annotate, call VLM, call brain,
   execute, log. It makes zero decisions about task strategy.

2. **The brain is the replaceable policy** — it interprets VLM text, runs agent swarms,
   and returns the next prompt. Swapping the brain changes all behavior.

3. **Swarms externalize cognition** — planning and execution are generated by LLM
   prompts, not Python heuristics. Python only parses and transports.

4. **Mid-swarm execution closes the loop** — agents can act and re-observe within
   a single turn, preventing drift from stale state.

5. **No Python data slicing** — every agent receives a self-contained message via
   its system prompt and user message. Python does not build different context
   views for different agents.

6. **Visual overlays are first-class** — the browser renders overlays onto screenshots
   before the VLM sees them. Overlays are both human-readable and VLM-readable.

7. **Empty responses are not fatal** — the engine passes every VLM response
   (including empty) to the brain. The brain decides what to do.

---

## AI Assistant Prompt

The following prompt can be used in conversations with AI assistants (ChatGPT,
Claude, Grok) to provide full context about the Franz-AI framework when
developing new brain files. Copy everything between the `---` markers:

---

### Franz-AI Brain Development Context Prompt

```
I am developing a brain module for Franz-AI, a Windows GUI automation framework.
Here is everything you need to know about the system to help me write brain code.

ARCHITECTURE:
Franz-AI has four immutable plumbing files and one swappable brain file:
- franz.py: Entry point, global queues, action/overlay helpers, brain loader
- router.py: Engine loop (capture->annotate->VLM->brain->execute->log), HTTP server, VLM calls
- win32.py: Pure ctypes Win32 screen capture, mouse/keyboard input, region selector
- panel.html: Browser-based UI that renders overlays onto screenshots and sends them back
- brain*.py: The ONLY file I modify. Contains all task-specific intelligence.

ENGINE LOOP (runs forever, one iteration = one turn):
1. Capture screenshot via win32.py subprocess (outputs raw PNG)
2. Send raw PNG + overlay list to panel.html via HTTP
3. Panel renders overlays on canvas, composites, POSTs annotated PNG back
4. Engine calls VLM with annotated PNG + previous brain output as user_text
5. Engine calls brain.on_vlm_response(vlm_text) -> returns next user_text
6. Engine drains action queue, executes via win32.py subprocesses
7. Engine drains overlay queue for next frame's overlays
8. Back to step 1

BRAIN CONTRACT:
def on_vlm_response(text: str) -> str
  Input: VLM's text description of the current annotated screenshot (may be empty string)
  Output: The next user_text that becomes the VLM prompt for the next turn
  Side effects: Can queue actions, overlays, log swarm messages, execute mid-swarm

COORDINATE SYSTEM:
All coordinates are normalized [0, 1000]:
- (0,0) = top-left of capture region
- (1000,1000) = bottom-right of capture region
- Resolution-independent

AVAILABLE ACTIONS (queue via franz.actions()):
franz.click(x, y), franz.double_click(x, y), franz.right_click(x, y)
franz.type_text("text"), franz.press_key("enter"), franz.hotkey("ctrl+s")
franz.scroll_up(x, y), franz.scroll_down(x, y)
franz.drag_start(x, y), franz.drag_end(x, y)

AVAILABLE OVERLAYS (queue via franz.overlays()):
franz.dot(x, y, label, color), franz.box(x1, y1, x2, y2, label, stroke_color)
franz.line([[x1,y1],[x2,y2]], label, color)
Custom dict format supports: points, closed, stroke, fill, stroke_width, dash,
opacity, glow, label, label_position, label_style (font_size, bg, color, align)

SWARM LOGGING:
franz.swarm_message(agent_name, direction, text, image_b64="", system="")
Direction: "input", "output", "error"
Messages appear in the panel's Swarm Wire and are saved to turns.txt

MID-SWARM EXECUTION:
The brain can call win32.py directly during on_vlm_response to execute actions
before returning. This allows: execute drags -> capture fresh screenshot ->
send to next agent who sees updated state. Use subprocess.run() with
[sys.executable, str(franz.WIN32_PATH), "drag", "--from_pos", "x,y", "--to_pos", "x,y"]

CONFIGURATION:
Brain exposes config as module-level variables. The engine reads them via
franz.cfg(brain, "NAME", default, cast). Key settings:
- VLM_ENDPOINT_URL, VLM_MODEL_NAME, VLM_TEMPERATURE, VLM_MAX_TOKENS
- VLM_REQUEST_DELAY (seconds between API calls, prevents server overload)
- CAPTURE_REGION, CAPTURE_WIDTH, CAPTURE_HEIGHT
- SYSTEM_PROMPT (for the engine's VISIONARY VLM call)
- SHOW_CURSOR, CURSOR_COLOR

DESIGN RULES:
1. Python only provides plumbing. All task intelligence lives in LLM agent prompts.
2. No Python state tracking that drives agent behavior (tracking for overlays is OK).
3. No Python fallback logic or hardcoded plans.
4. No Python context assembly that builds different views for different agents.
5. Each agent call is self-contained: system prompt + user message (+ optional image).
6. Empty VLM responses flow to the brain normally (not treated as errors).
7. Visual overlays should help both the human and the VLM understand state.

HOW TO CALL LLM AGENTS FROM BRAIN:
Use urllib.request to POST to the VLM endpoint with OpenAI-compatible format:
{"model": "...", "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]}
For multimodal: user content is [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
Always call franz.swarm_message() before and after to log to the panel.

INVESTIGATION METHODS:
- CLI output shows turn summaries and agent responses
- logs/<session>/turns.txt has complete INPUT/OUTPUT/SWARM records per turn
- logs/<session>/*.png are annotated screenshots per turn
- Panel SAVE button exports full state with inlined images
- VLM server logs show request format, token counts, context usage, errors
- Compare: what VLM reported (OUTPUT) vs what was actually on screen (PNG files)
- Watch for: context overflow, repeating inputs, hallucinated observations, empty responses
```

---

## License

MIT

---

## Contributing

Brain contributions are welcome. The plumbing files (`franz.py`, `router.py`,
`win32.py`, `panel.html`) should not be modified — all innovation happens in
brain files and their agent prompts.
```