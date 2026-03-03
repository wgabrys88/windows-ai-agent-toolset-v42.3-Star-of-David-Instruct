"""
brain_bench_creative.py -- Franz-AI Creative Benchmark Brain

Goal: Draw a Star of David on the canvas.
The agents are NOT given exact coordinates or stroke lists.
They must deduce the geometry and plan their own strokes.

Architecture:
  PLANNER  - Sees the canvas, decides what strokes to draw next (text output)
  EXECUTOR - Converts the PLANNER's text description into drag() commands

The engine's VISIONARY provides observation each turn.
Python only: calls agents, parses drags, executes, renders overlays.
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
VLM_MAX_TOKENS: int = 300
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

COLOR_STROKE: str = "#00ff00"
COLOR_INFO: str = "#ffaa00"

# -- Agent System Prompts -----------------------------------------------------

SYSTEM_PROMPT: str = """You observe a canvas where a drawing is being made with a black brush.
Describe ONLY what you physically see. Be brief (under 100 words).

Visual guide:
- BLACK lines = drawn by the brush on the actual canvas.
- GREEN lines = overlay markers showing strokes drawn in previous turns.
- RED crosshair = current cursor position.

Your task:
1. Describe every black line or shape you see with approximate coordinates.
2. Say how many separate line segments are visible.
3. Describe the overall shape being formed, if recognizable.
4. Do NOT say what should be drawn next. Only report what IS visible.
5. If the canvas is blank, say it is blank."""

PLANNER_PROMPT: str = """You are PLANNER, a geometry expert who plans drawing strokes.

GOAL: Draw a Star of David (hexagram) on a canvas.
The canvas coordinate system: (0,0) is top-left, (1000,1000) is bottom-right.

A Star of David is two overlapping equilateral triangles:
- One triangle points UP (vertex at top-center, base at bottom)
- One triangle points DOWN (vertex at bottom-center, base at top)

The triangles should be large, centered on the canvas, and clearly visible.
They should overlap to form the classic six-pointed star shape.

You receive a VISIONARY REPORT describing what is currently on the canvas.
Based on what has already been drawn, decide what to draw next.

OUTPUT FORMAT (3-6 lines of plain text):
- Line 1: What has been drawn so far (brief summary)
- Line 2: What needs to be drawn next (specific line segments with coordinates)
- Line 3+: The exact strokes to draw, described as:
  "Draw line from (x1,y1) to (x2,y2)"

Think about the geometry:
- The star should be centered around (500,500)
- Use coordinates that create a balanced, symmetric hexagram
- Each triangle has 3 sides = 3 strokes
- Total: 6 strokes for the complete star

Be precise with coordinates. Do NOT output drag() commands."""

EXECUTOR_PROMPT: str = """You convert drawing instructions into brush strokes.
Canvas: (0,0) top-left, (1000,1000) bottom-right.

Output ONLY drag lines in this exact format:
drag(x1,y1,x2,y2)

Rules:
- Read the PLANNER's instructions carefully.
- Convert each "Draw line from (x1,y1) to (x2,y2)" into a drag command.
- Generate 1 to 6 strokes per call.
- Use the exact coordinates the PLANNER specified.
- Green lines on the canvas are already drawn. Do not redraw them.
- Output NOTHING except drag lines. No text. No explanation."""

FIRST_TURN_PROMPT: str = """The canvas is blank. No strokes have been drawn yet.
Describe what you see. The goal is to draw a Star of David (hexagram)."""


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
    max_tokens: int = 300,
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


# -- Overlay Rendering --------------------------------------------------------

_all_executed_drags: list[tuple[int, int, int, int, int]] = []


def _queue_stroke_overlays() -> None:
    """Render all executed strokes as green overlay lines with turn labels."""
    for x1, y1, x2, y2, turn in _all_executed_drags:
        franz.overlays({
            "points": [[x1, y1], [x2, y2]],
            "closed": False,
            "stroke": COLOR_STROKE,
            "fill": "",
            "stroke_width": 2,
            "label": "t" + str(turn),
            "label_position": [(x1 + x2) // 2, (y1 + y2) // 2],
            "label_style": {
                "font_size": 8,
                "bg": "#000000",
                "color": COLOR_STROKE,
                "align": "center",
            },
            "opacity": 0.8,
        })


def _queue_info_overlay(text: str) -> None:
    """Show an info message in the top-left corner."""
    franz.overlays({
        "points": [[10, 10]],
        "closed": False,
        "stroke": COLOR_INFO,
        "fill": "",
        "label": text,
        "label_position": [10, 10],
        "label_style": {
            "font_size": 12,
            "bg": "#000000",
            "color": "#ffffff",
            "align": "left",
        },
        "opacity": 1.0,
    })


# -- Swarm Execution ----------------------------------------------------------

_turn_count: int = 0
_max_turns: int = 20


def _run_swarm(visionary_report: str) -> str:
    """
    Two-agent swarm: PLANNER decides what to draw, EXECUTOR produces drags.
    Python only parses and executes -- all intelligence is in the prompts.
    """
    global _turn_count

    if _turn_count > _max_turns:
        _queue_stroke_overlays()
        _queue_info_overlay("Max turns reached: " + str(_max_turns))
        return "Maximum turns reached. Describe the final state of the drawing."

    image_b64: str = _latest_screenshot_b64()

    # Step 1: PLANNER sees the canvas and decides what to draw
    planner_input: str = "VISIONARY REPORT:\n" + visionary_report

    planner_response: str = _call_agent(
        "PLANNER",
        PLANNER_PROMPT,
        planner_input,
        temperature=0.3,
        top_p=0.8,
        max_tokens=400,
        image_b64=image_b64,
    )

    print(f"  PLANNER: {planner_response[:300]}")

    if not planner_response:
        _queue_stroke_overlays()
        _queue_info_overlay("Turn " + str(_turn_count) + " - PLANNER returned empty")
        return (
            "The PLANNER could not determine next steps. "
            "Look at the canvas and describe what you see."
        )

    # Step 2: EXECUTOR converts PLANNER's text into drag commands
    executor_input: str = "PLANNER INSTRUCTIONS:\n" + planner_response

    executor_response: str = _call_agent(
        "EXECUTOR",
        EXECUTOR_PROMPT,
        executor_input,
        temperature=0.15,
        top_p=0.8,
        max_tokens=200,
        image_b64=image_b64,
    )

    print(f"  EXECUTOR: {executor_response[:300]}")

    drags: list[tuple[int, int, int, int]] = _parse_drags(executor_response)

    if not drags:
        print("  WARNING: EXECUTOR produced no valid drags.")
        _queue_stroke_overlays()
        _queue_info_overlay("Turn " + str(_turn_count) + " - no drags parsed")
        return (
            "The EXECUTOR could not produce valid strokes. "
            "Look at the canvas and describe what you see. "
            "What has been drawn so far toward the Star of David?"
        )

    # Execute the drags mid-swarm
    _execute_drags(drags)
    time.sleep(0.3)

    # Track all executed drags for overlay rendering
    for drag in drags:
        _all_executed_drags.append((drag[0], drag[1], drag[2], drag[3], _turn_count))
        print(f"    [DRAW] ({drag[0]},{drag[1]}) -> ({drag[2]},{drag[3]})")

    # Render overlays
    _queue_stroke_overlays()
    _queue_info_overlay(
        "Turn " + str(_turn_count) + " | "
        + str(len(_all_executed_drags)) + " strokes total"
    )

    return (
        "Look at the canvas now. Describe what you see.\n"
        "Is the Star of David (hexagram) taking shape?\n"
        "How many line segments are visible?\n"
        "What is still missing to complete the star?"
    )


# -- Brain Contract -----------------------------------------------------------

def on_vlm_response(text: str) -> str:
    """
    Brain entry point. Called by the engine with the VLM's observation.
    Returns the next user_text prompt for the VLM.
    """
    global _turn_count
    _turn_count += 1

    if _turn_count == 1:
        return FIRST_TURN_PROMPT

    return _run_swarm(text)
