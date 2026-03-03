import http.server
import importlib.util
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE: Path = Path(__file__).resolve().parent
PANEL_PATH: Path = HERE / "panel.html"
WIN32_PATH: Path = HERE / "win32.py"

NORM: int = 1000

action_queue: queue.Queue[dict[str, Any]] = queue.Queue()
overlay_queue: queue.Queue[dict[str, Any]] = queue.Queue()
swarm_queue: queue.Queue[dict[str, Any]] = queue.Queue()

_brain_annotate_request: queue.Queue[dict[str, Any]] = queue.Queue()
_brain_annotate_result: queue.Queue[str] = queue.Queue()

_runtime_overrides: dict[str, Any] = {}


def _clamp(value: int) -> int:
    return max(0, min(NORM, value))


def actions(action: dict[str, Any]) -> None:
    action_queue.put(action)


def overlays(overlay: dict[str, Any]) -> None:
    overlay_queue.put(overlay)


def swarm_message(
    agent: str,
    direction: str,
    text: str,
    image_b64: str = "",
    system: str = "",
) -> None:
    swarm_queue.put({
        "agent": agent,
        "direction": direction,
        "text": text,
        "image_b64": image_b64,
        "system": system,
        "ts": time.time(),
    })


def request_annotation(image_b64: str, agent_overlays: list[dict[str, Any]]) -> str:
    while not _brain_annotate_result.empty():
        try:
            _brain_annotate_result.get_nowait()
        except queue.Empty:
            break
    _brain_annotate_request.put({
        "image_b64": image_b64,
        "overlays": agent_overlays,
    })
    try:
        return _brain_annotate_result.get(timeout=120)
    except queue.Empty:
        print("WARNING: Brain annotation timeout after 120s", file=sys.stderr)
        raise


def click(x: int, y: int) -> dict[str, Any]:
    return {"type": "click", "x": _clamp(x), "y": _clamp(y)}


def double_click(x: int, y: int) -> dict[str, Any]:
    return {"type": "double_click", "x": _clamp(x), "y": _clamp(y)}


def right_click(x: int, y: int) -> dict[str, Any]:
    return {"type": "right_click", "x": _clamp(x), "y": _clamp(y)}


def type_text(text: str) -> dict[str, Any]:
    return {"type": "type_text", "params": text}


def press_key(name: str) -> dict[str, Any]:
    return {"type": "press_key", "params": name}


def hotkey(combo: str) -> dict[str, Any]:
    return {"type": "hotkey", "params": combo}


def scroll_up(x: int, y: int) -> dict[str, Any]:
    return {"type": "scroll_up", "x": _clamp(x), "y": _clamp(y)}


def scroll_down(x: int, y: int) -> dict[str, Any]:
    return {"type": "scroll_down", "x": _clamp(x), "y": _clamp(y)}


def drag_start(x: int, y: int) -> dict[str, Any]:
    return {"type": "drag_start", "x": _clamp(x), "y": _clamp(y)}


def drag_end(x: int, y: int) -> dict[str, Any]:
    return {"type": "drag_end", "x": _clamp(x), "y": _clamp(y)}


def dot(x: int, y: int, label: str = "", color: str = "#00ff00") -> dict[str, Any]:
    return {
        "points": [[x, y]],
        "closed": False,
        "stroke": color,
        "fill": "",
        "label": label,
        "label_position": [x, y],
        "label_style": {"font_size": 10, "bg": "", "color": color, "align": "left"},
    }


def box(
    x1: int, y1: int, x2: int, y2: int,
    label: str = "", stroke_color: str = "#ff6600", fill_color: str = "",
) -> dict[str, Any]:
    return {
        "points": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
        "closed": True,
        "stroke": stroke_color,
        "fill": fill_color,
        "label": label,
        "label_position": [x1, y1],
        "label_style": {"font_size": 10, "bg": "", "color": stroke_color, "align": "left"},
    }


def line(points: list[list[int]], label: str = "", color: str = "#4488ff") -> dict[str, Any]:
    return {
        "points": points,
        "closed": False,
        "stroke": color,
        "fill": "",
        "label": label,
        "label_position": points[0] if points else [0, 0],
        "label_style": {"font_size": 10, "bg": "", "color": color, "align": "left"},
    }


def drain_queue(q: queue.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items


def _load_module(name: str, filename: str) -> Any:
    filepath: Path = HERE / filename
    if not filepath.exists():
        print(f"ERROR: {filename} not found in {HERE}")
        raise SystemExit(1)
    spec = importlib.util.spec_from_file_location(name, str(filepath))
    if spec is None or spec.loader is None:
        print(f"ERROR: cannot load {filename}")
        raise SystemExit(1)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cfg(brain: Any, name: str, default: Any, cast: type = str) -> Any:
    raw: Any = _runtime_overrides.get(name, getattr(brain, name, default))
    return cast(raw)


def _pick_brain() -> str:
    candidates: list[Path] = sorted(HERE.glob("brain*.py"))
    if not candidates:
        print(f"ERROR: No brain*.py files found in {HERE}")
        raise SystemExit(1)
    if len(candidates) == 1:
        print(f"Using brain: {candidates[0].name}")
        return candidates[0].name
    print("\nAvailable brains:")
    for idx, filepath in enumerate(candidates):
        print(f"  [{idx + 1}] {filepath.name}")
    while True:
        choice: str = input(f"\nSelect brain [1-{len(candidates)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            selected: str = candidates[int(choice) - 1].name
            print(f"Selected: {selected}")
            return selected
        print("Invalid choice, try again.")


def _run_select_region() -> tuple[str, int]:
    import subprocess
    proc: subprocess.CompletedProcess[bytes] = subprocess.run(
        [sys.executable, str(WIN32_PATH), "select_region"],
        capture_output=True,
    )
    if proc.returncode == 2:
        return "", 2
    if proc.returncode != 0 or not proc.stdout:
        return "", proc.returncode
    return proc.stdout.decode("ascii").strip(), 0


def main() -> None:
    brain_filename: str = _pick_brain()

    print("Select capture region (drag), right-click for full screen, Escape to quit.")
    region_str, exit_code = _run_select_region()

    if exit_code == 2:
        print("Cancelled.")
        raise SystemExit(0)

    if region_str:
        print(f"Region selected: {region_str}")
        _runtime_overrides["CAPTURE_REGION"] = region_str
    else:
        print("Full screen mode.")
        _runtime_overrides["CAPTURE_REGION"] = ""

    sys.modules["franz"] = sys.modules[__name__]
    brain: Any = _load_module("brain", brain_filename)

    if not hasattr(brain, "on_vlm_response"):
        print(f"ERROR: {brain_filename} missing required: on_vlm_response")
        raise SystemExit(1)

    router: Any = _load_module("router", "router.py")

    session = router.SessionLog.create()
    host: str = cfg(brain, "SERVER_HOST", "127.0.0.1")
    port: int = cfg(brain, "SERVER_PORT", 1234, int)

    print(f"\nFranz starting on http://{host}:{port}")
    print(f"VLM: {cfg(brain, 'VLM_ENDPOINT_URL', '?')}")
    print(f"Region: {cfg(brain, 'CAPTURE_REGION', '') or 'full screen'}")
    print(f"Session: {session.session_dir}")
    print(f"\nOpen http://{host}:{port} in Chrome to start.\n")

    engine: threading.Thread = threading.Thread(
        target=router.engine_loop, args=(brain, session), daemon=True,
    )
    engine.start()

    server: http.server.ThreadingHTTPServer = http.server.ThreadingHTTPServer(
        (host, port), router.FranzHandler,
    )
    print(f"Running at http://{host}:{port}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()
        print("Franz stopped.")


if __name__ == "__main__":
    main()
