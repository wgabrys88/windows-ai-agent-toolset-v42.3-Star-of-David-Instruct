"""
brain_bench.py -- Franz-AI Benchmark Brain: Star of David

Draws two overlapping triangles forming a hexagram, plus a center marker.
All intelligence lives in agent prompts. Python only provides plumbing:
  - Call agents (with or without image)
  - Parse drag commands from EXECUTOR output
  - Execute drags mid-swarm and re-capture
  - Queue visual overlays for completed strokes

No Python state tracking, no context assembly, no fallback logic,
no data slicing between agents. The swarm orchestrates itself.
"""

import base64
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import franz

# -- VLM / Server Config -----------------------------------------------------

VLM_ENDPOINT_URL: str = "http://127.0.0.1:1235/v1/chat/completions"
VLM_MODEL_NAME: str = "huihui-qwen3-vl-2b-instruct-abliterated"
VLM_TEMPERATURE: float = 0.3
VLM_TOP_P: float = 0.8
VLM_MAX_TOKENS: int = 350
VLM_TIMEOUT: int = 360
VLM_REQUEST_DELAY: float = 1.0

SERVER_HOST: str = "127.0.0.1"
SERVER_PORT: int = 1234

# -- Capture Config -----------------------------------------------------------

CAPTURE_REGION: str = ""
CAPTURE_WIDTH: int = 640
CAPTURE_HEIGHT: int = 640
CAPTURE_DELAY_SECONDS: float = 1.5
ACTION_DELAY_SECONDS: float = 0.1
SHOW_CURSOR: bool = True
DEFAULT_CURSOR_POS: int = 500

# -- Cursor Appearance --------------------------------------------------------

CURSOR_COLOR: str = "#ff4444"
CURSOR_ARM: int = 14
CURSOR_FONT_SIZE: int = 12
CURSOR_LABEL_BG: str = "#000000"
CURSOR_SHOW_LABEL: bool = True
CURSOR_LABEL_OFFSET: int = 18
CURSOR_LABEL_LIMIT: int = 980

# -- Visual Feedback ----------------------------------------------------------

COLOR_DONE: str = "#00ff00"
COLOR_PLANNED: str = "#ffaa00"
COLOR_TARGET: str = "#4488ff"

# -- The Benchmark Target (reference for overlay rendering) -------------------

TARGET_STROKES: list[tuple[int, int, int, int, str]] = [
    # Upward triangle
    (500,  100,  150, 700, "up-1"),
    (150,  700,  850, 700, "up-2"),
    (850,  700,  500, 100, "up-3"),
    # Downward triangle
    (500,  900,  850, 300, "down-1"),
    (850,  300,  150, 300, "down-2"),
    (150,  300,  500, 900, "down-3"),
    # Center dot (small cross)
    (480,  500,  520, 500, "center-h"),
    (500,  480,  500, 520, "center-v"),
]

# -- Agent System Prompts -----------------------------------------------------

SYSTEM_PROMPT: str = """You observe a canvas where a geometric pattern is being drawn with a black brush.
Describe ONLY what you physically see. Be brief (under 120 words).

Visual guide:
- BLACK lines/shapes = drawn by the brush on the actual canvas.
- GREEN lines = overlay markers showing previously completed strokes.
- ORANGE lines = overlay markers showing planned-but-not-yet-drawn strokes.
- BLUE lines = overlay showing the full target pattern for reference.
- RED crosshair = current cursor position.

Your task:
1. List each BLACK stroke you see (approximate start and end coordinates).
2. Count total completed strokes (black + green markers).
3. Count remaining strokes (orange markers).
4. Do NOT describe what should be drawn next. Only report what IS visible.
5. Do NOT repeat the plan or instructions back. Only describe the image."""

SWARM_PROMPT: str = """You are CONDUCTOR, the sole decision-maker for a drawing benchmark.
You receive a VISIONARY REPORT describing what is currently on the canvas.
You also see the canvas image with color-coded overlay guides.

TARGET PATTERN -- Star of David (hexagram):
  Upward triangle:
    Stroke up-1: drag(500,100, 150,700)
    Stroke up-2: drag(150,700, 850,700)
    Stroke up-3: drag(850,700, 500,100)
  Downward triangle:
    Stroke down-1: drag(500,900, 850,300)
    Stroke down-2: drag(850,300, 150,300)
    Stroke down-3: drag(150,300, 500,900)
  Center mark:
    Stroke center-h: drag(480,500, 520,500)
    Stroke center-v: drag(500,480, 500,520)

RULES:
- Compare the VISIONARY REPORT against the target strokes above.
- Determine which strokes are already done (visible as black lines or green markers).
- Output the NEXT 1-4 strokes to draw, as drag commands.
- Use EXACT coordinates from the target. Do not approximate.
- Do NOT redraw strokes that are already visible (green markers = done).
- If ALL 8 strokes are complete, output only the word DONE.

OUTPUT FORMAT -- nothing else, no explanation, no text:
drag(x1,y1,x2,y2)
drag(x1,y1,x2,y2)
...
OR:
DONE"""

FIRST_TURN_PROMPT: str = """The canvas is blank. No strokes have been drawn yet.
Describe what you see. The goal is to draw a Star of David pattern."""


# -- Helpers ------------------------------------------------------------------

def _capture_region() -> str:
    return getattr(franz, "_runtime_overrides", {}).get("CAPTURE_REGION", "") or CAPTURE_REGION


def _execute_drags(drags: list[tuple[int, int, int, int]]) -> None:
    """Execute drag strokes on the real screen via win32."""
    region: str = _capture_region()
    for x1, y1, x2, y2 in drags:
        cmd: list[str] = [
            sys.executable, str(franz.WIN32_PATH), "drag",
            "--from_pos", f"{x1},{y1}",
            "--to_pos", f"{x2},{y2}",
        ]
        if region:
            cmd.extend(["--region", region])
        subprocess.run(cmd, capture_output=True)


def _capture_fresh_b64() -> str:
    """Take a live screenshot and return as base64."""
    region: str = _capture_region()
    cmd: list[str] = [
        sys.executable, str(franz.WIN32_PATH), "capture",
        "--width", str(CAPTURE_WIDTH),
        "--height", str(CAPTURE_HEIGHT),
    ]
    if region:
        cmd.extend(["--region", region])
    proc: subprocess.CompletedProcess[bytes] = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return ""
    return base64.b64encode(proc.stdout).decode("ascii")


def _latest_screenshot_b64() -> str:
    """Read the most recent annotated screenshot from the session log."""
    logs_root: Path = franz.HERE / "logs"
    if not logs_root.exists():
        return ""
    session_dirs: list[Path] = sorted(
        (d for d in logs_root.iterdir() if d.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not session_dirs:
        return ""
    png_files: list[Path] = sorted(
        session_dirs[0].glob("*.png"),
        key=lambda p: p.name,
        reverse=True,
    )
    if not png_files:
        return ""
    return base64.b64encode(png_files[0].read_bytes()).decode("ascii")


def _call_agent(
    agent_name: str,
    system_prompt: str,
    user_text: str,
    *,
    temperature: float = 0.3,
    top_p: float = 0.8,
    max_tokens: int = 350,
    image_b64: str = "",
) -> str:
    """Call an LLM agent. Logs to swarm wire. Returns response text."""
    time.sleep(VLM_REQUEST_DELAY)

    franz.swarm_message(agent_name, "input", user_text, image_b64, system_prompt)

    if image_b64:
        user_content: Any = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]
    else:
        user_content = user_text

    body: bytes = json.dumps({
        "model": VLM_MODEL_NAME,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")

    req: urllib.request.Request = urllib.request.Request(
        VLM_ENDPOINT_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=VLM_TIMEOUT) as resp:
            resp_obj: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        choices: list[Any] = resp_obj.get("choices", [])
        if choices and isinstance(choices[0], dict):
            msg: Any = choices[0].get("message", {})
            content: str = msg.get("content", "") if isinstance(msg, dict) else ""
            franz.swarm_message(agent_name, "output", content)
            return content.strip()
        franz.swarm_message(agent_name, "output", "")
        return ""
    except Exception as exc:
        franz.swarm_message(agent_name, "error", str(exc))
        print(f"Agent call error ({agent_name}): {exc}", file=sys.stderr)
        return ""


def _parse_drags(raw: str) -> list[tuple[int, int, int, int]]:
    """Extract drag(x1,y1,x2,y2) commands from text."""
    drags: list[tuple[int, int, int, int]] = []
    for line in raw.strip().splitlines():
        cleaned: str = line.strip()
        if not cleaned.lower().startswith("drag(") or ")" not in cleaned:
            continue
        inner: str = cleaned[5:cleaned.index(")")]
        parts: list[str] = inner.split(",")
        if len(parts) < 4:
            continue
        try:
            vals: list[int] = [int(float(p.strip())) for p in parts[:4]]
        except (ValueError, TypeError):
            continue
        if all(0 <= v <= 1000 for v in vals):
            drags.append((vals[0], vals[1], vals[2], vals[3]))
    return drags


def _is_done(raw: str) -> bool:
    """Check if the CONDUCTOR signaled completion."""
    cleaned: str = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return cleaned.upper().strip() == "DONE"


def _match_stroke(
    drag: tuple[int, int, int, int],
    tolerance: int = 40,
) -> str | None:
    """Match an executed drag to a known target stroke by coordinate proximity."""
    dx1, dy1, dx2, dy2 = drag
    for tx1, ty1, tx2, ty2, label in TARGET_STROKES:
        # Match forward direction
        if (abs(dx1 - tx1) <= tolerance and abs(dy1 - ty1) <= tolerance
                and abs(dx2 - tx2) <= tolerance and abs(dy2 - ty2) <= tolerance):
            return label
        # Match reverse direction
        if (abs(dx1 - tx2) <= tolerance and abs(dy1 - ty2) <= tolerance
                and abs(dx2 - tx1) <= tolerance and abs(dy2 - ty1) <= tolerance):
            return label
    return None


# -- Overlay Rendering --------------------------------------------------------

def _queue_target_overlays(completed_labels: set[str]) -> None:
    """
    Render the full target pattern as visual overlays:
    - GREEN for completed strokes
    - ORANGE for planned/remaining strokes
    - BLUE faint for full reference pattern
    """
    for tx1, ty1, tx2, ty2, label in TARGET_STROKES:
        if label in completed_labels:
            franz.overlays({
                "points": [[tx1, ty1], [tx2, ty2]],
                "closed": False,
                "stroke": COLOR_DONE,
                "fill": "",
                "stroke_width": 2,
                "label": "[OK] " + label,
                "label_position": [tx1, ty1],
                "label_style": {
                    "font_size": 9,
                    "bg": "#000000",
                    "color": COLOR_DONE,
                    "align": "left",
                },
                "opacity": 0.9,
            })
        else:
            franz.overlays({
                "points": [[tx1, ty1], [tx2, ty2]],
                "closed": False,
                "stroke": COLOR_TARGET,
                "fill": "",
                "stroke_width": 1,
                "dash": [6, 4],
                "label": "",
                "label_position": [tx1, ty1],
                "label_style": {
                    "font_size": 8,
                    "bg": "",
                    "color": COLOR_TARGET,
                    "align": "left",
                },
                "opacity": 0.35,
                "glow": False,
            })
            franz.overlays({
                "points": [[tx1, ty1], [tx2, ty2]],
                "closed": False,
                "stroke": COLOR_PLANNED,
                "fill": "",
                "stroke_width": 1,
                "label": label,
                "label_position": [
                    (tx1 + tx2) // 2,
                    (ty1 + ty2) // 2,
                ],
                "label_style": {
                    "font_size": 8,
                    "bg": "#000000",
                    "color": COLOR_PLANNED,
                    "align": "center",
                },
                "opacity": 0.7,
            })


def _queue_progress_overlay(completed: int, total: int) -> None:
    """Show a progress indicator in the top-left corner."""
    pct: int = round(100 * completed / total) if total > 0 else 0
    franz.overlays({
        "points": [[10, 10]],
        "closed": False,
        "stroke": COLOR_DONE if completed == total else COLOR_PLANNED,
        "fill": "",
        "label": f"{completed}/{total} strokes ({pct}%)",
        "label_position": [10, 10],
        "label_style": {
            "font_size": 12,
            "bg": "#000000",
            "color": COLOR_DONE if completed == total else "#ffffff",
            "align": "left",
        },
        "opacity": 1.0,
    })


# -- Swarm Execution ----------------------------------------------------------

_completed_labels: set[str] = set()


def _run_swarm(visionary_report: str) -> str:
    """
    Single-agent swarm: CONDUCTOR sees the canvas + visionary report,
    decides what to draw next, outputs drag commands.
    Python only parses and executes -- zero decision-making.
    """
    global _completed_labels

    if len(_completed_labels) >= len(TARGET_STROKES):
        _queue_target_overlays(_completed_labels)
        _queue_progress_overlay(len(_completed_labels), len(TARGET_STROKES))
        return "The benchmark pattern is complete. Describe the final result."

    image_b64: str = _latest_screenshot_b64()

    user_message: str = "VISIONARY REPORT:\n" + visionary_report

    conductor_response: str = _call_agent(
        "CONDUCTOR",
        SWARM_PROMPT,
        user_message,
        temperature=0.15,
        top_p=0.8,
        max_tokens=200,
        image_b64=image_b64,
    )

    print(f"  CONDUCTOR: {conductor_response[:300]}")

    if _is_done(conductor_response):
        print("  CONDUCTOR signaled DONE -- benchmark complete!")
        _completed_labels = {label for _, _, _, _, label in TARGET_STROKES}
        _queue_target_overlays(_completed_labels)
        _queue_progress_overlay(len(_completed_labels), len(TARGET_STROKES))
        return "The benchmark pattern is complete. Describe the final result."

    drags: list[tuple[int, int, int, int]] = _parse_drags(conductor_response)

    if not drags:
        print("  WARNING: CONDUCTOR produced no valid drags. Passing observation back.")
        _queue_target_overlays(_completed_labels)
        _queue_progress_overlay(len(_completed_labels), len(TARGET_STROKES))
        return (
            "The previous attempt produced no valid strokes. "
            "Look at the canvas and describe what you see. "
            "Which strokes from the Star of David pattern are already drawn?"
        )

    _execute_drags(drags)
    time.sleep(0.3)

    for drag in drags:
        matched_label: str | None = _match_stroke(drag)
        if matched_label:
            _completed_labels.add(matched_label)
            print(f"    [OK] Matched stroke: {matched_label}")
        else:
            print(f"    [??] Unmatched drag: {drag}")

    if len(drags) >= 3:
        time.sleep(0.5)
        fresh_b64: str = _capture_fresh_b64()
        if fresh_b64:
            verify_response: str = _call_agent(
                "CONDUCTOR",
                SWARM_PROMPT,
                "VISIONARY REPORT:\nJust executed " + str(len(drags)) + " strokes. Verify the canvas and output additional strokes if needed, or DONE if the pattern is complete.",
                temperature=0.15,
                top_p=0.8,
                max_tokens=200,
                image_b64=fresh_b64,
            )
            print(f"  CONDUCTOR (verify): {verify_response[:300]}")

            if not _is_done(verify_response):
                extra_drags: list[tuple[int, int, int, int]] = _parse_drags(verify_response)
                if extra_drags:
                    _execute_drags(extra_drags)
                    for drag in extra_drags:
                        matched: str | None = _match_stroke(drag)
                        if matched:
                            _completed_labels.add(matched)
                            print(f"    [OK] Matched stroke: {matched}")
            else:
                _completed_labels = {label for _, _, _, _, label in TARGET_STROKES}

    _queue_target_overlays(_completed_labels)
    _queue_progress_overlay(len(_completed_labels), len(TARGET_STROKES))

    return (
        "Look at the canvas now. Describe what you see.\n"
        "How many of the Star of David strokes are visible?\n"
        "Which strokes are missing?"
    )


# -- Brain Contract -----------------------------------------------------------

def on_vlm_response(text: str) -> str:
    """
    Brain entry point. Called by the engine with the VLM's observation.
    Returns the next user_text prompt for the VLM.
    """
    if not text or ("blank" in text.lower() and not _completed_labels):
        return FIRST_TURN_PROMPT

    return _run_swarm(text)
