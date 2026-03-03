import base64
import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import franz

HERE: Path = franz.HERE
PANEL_PATH: Path = franz.PANEL_PATH
WIN32_PATH: Path = franz.WIN32_PATH

MIN_ANNOTATION_LENGTH: int = 100
MAX_SSE_SUBSCRIBERS: int = 3


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


class _EventBus:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._subscribers: list[dict[str, Any]] = []

    def subscribe(self) -> dict[str, Any]:
        import queue as _q
        sub: dict[str, Any] = {
            "queue": _q.Queue(),
            "active": True,
        }
        with self._lock:
            if len(self._subscribers) >= MAX_SSE_SUBSCRIBERS:
                oldest = self._subscribers[0]
                oldest["active"] = False
                try:
                    oldest["queue"].put_nowait(None)
                except Exception:
                    pass
                self._subscribers.pop(0)
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: dict[str, Any]) -> None:
        sub["active"] = False
        with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"event": event_type, "data": data}
        with self._lock:
            dead: list[dict[str, Any]] = []
            for sub in self._subscribers:
                if not sub["active"]:
                    dead.append(sub)
                    continue
                try:
                    sub["queue"].put_nowait(payload)
                except Exception:
                    dead.append(sub)
            for d in dead:
                d["active"] = False
                try:
                    self._subscribers.remove(d)
                except ValueError:
                    pass


_EVENT_BUS: _EventBus = _EventBus()


class SessionLog:
    def __init__(self, session_dir: Path, turns_file: Path) -> None:
        self.session_dir: Path = session_dir
        self.turns_file: Path = turns_file

    @staticmethod
    def create() -> "SessionLog":
        logs_root: Path = HERE / "logs"
        logs_root.mkdir(exist_ok=True)
        session_dir: Path = logs_root / _utc_stamp()
        session_dir.mkdir(exist_ok=True)
        return SessionLog(session_dir=session_dir, turns_file=session_dir / "turns.txt")

    def _append(self, text: str) -> None:
        with self.turns_file.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def write_turn(self, turn: int, label: str, text: str) -> None:
        self._append(f"[TURN {turn}] [{_utc_stamp()}] [{label}]\n{text}\n\n")

    def write_swarm(self, turn: int, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        parts: list[str] = [f"[TURN {turn}] [{_utc_stamp()}] [SWARM] ({len(messages)} messages)"]
        for msg in messages:
            agent: str = msg.get("agent", "?")
            direction: str = msg.get("direction", "?")
            text: str = msg.get("text", "")
            has_img: str = " [+IMG]" if msg.get("image_b64", "") else ""
            system: str = msg.get("system", "")
            ts: float = msg.get("ts", 0)
            ts_str: str = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                if ts else "--:--:--.---"
            )
            match direction:
                case "input":
                    parts.append(f"    >> {agent}{has_img} [{ts_str}]")
                    if system:
                        for sl in system.splitlines():
                            parts.append(f"       [SYS] {sl}")
                    for tl in text.splitlines():
                        parts.append(f"       {tl}")
                case "output":
                    parts.append(f"    << {agent} [{ts_str}]")
                    for tl in text.splitlines():
                        parts.append(f"       {tl}")
                case "error":
                    parts.append(f"    !! {agent} [{ts_str}] ERROR")
                    for tl in text.splitlines():
                        parts.append(f"       {tl}")
                case _:
                    parts.append(f"    -- {agent} {direction}{has_img} [{ts_str}]")
                    for tl in text.splitlines():
                        parts.append(f"       {tl}")
        parts.append("")
        self._append("\n".join(parts) + "\n")

    def save_png(self, data_b64: str) -> None:
        (self.session_dir / f"{_utc_stamp()}.png").write_bytes(base64.b64decode(data_b64))


class _ServerState:
    def __init__(self) -> None:
        self.phase: str = "init"
        self.turn: int = 0
        self.raw_b64: str = ""
        self.raw_seq: int = 0
        self.overlays: list[dict[str, Any]] = []
        self.pending_seq: int = 0
        self.annotated_seq: int = -1
        self.annotated_b64: str = ""
        self.annotated_ready: threading.Event = threading.Event()
        self.display_text: str = ""
        self.display_actions: list[dict[str, Any]] = []
        self.error_text: str = ""
        self.swarm_messages: list[dict[str, Any]] = []
        self.brain_ann_request: dict[str, Any] | None = None
        self.brain_ann_seq: int = 0
        self.brain_ann_pending_seq: int = 0
        self.brain_ann_done_seq: int = -1
        self.brain_ann_ready: threading.Event = threading.Event()
        self.brain_ann_result_b64: str = ""
        self.brain_ann_live_b64: str = ""
        self.lock: threading.Lock = threading.Lock()


_STATE: _ServerState = _ServerState()


class _PanelReady:
    def __init__(self) -> None:
        self._event: threading.Event = threading.Event()
        self._connected: bool = False

    def signal(self) -> None:
        if not self._connected:
            self._connected = True
            self._event.set()
            print("Panel connected.")

    def wait(self) -> None:
        print("Waiting for panel to connect...")
        self._event.wait()


_BRIDGE: _PanelReady = _PanelReady()


def _publish_state() -> None:
    with _STATE.lock:
        _EVENT_BUS.publish("state", {
            "phase": _STATE.phase,
            "turn": _STATE.turn,
            "pending_seq": _STATE.pending_seq,
            "annotated_seq": _STATE.annotated_seq,
            "raw_seq": _STATE.raw_seq,
            "error": _STATE.error_text,
            "swarm_count": len(_STATE.swarm_messages),
            "brain_ann_seq": _STATE.brain_ann_pending_seq,
            "brain_ann_done": _STATE.brain_ann_done_seq,
        })


def _subprocess_capture(brain: Any) -> str:
    cmd: list[str] = [sys.executable, str(WIN32_PATH), "capture"]
    region: str = franz.cfg(brain, "CAPTURE_REGION", "")
    if region:
        cmd.extend(["--region", region])
    width: int = franz.cfg(brain, "CAPTURE_WIDTH", 640, int)
    height: int = franz.cfg(brain, "CAPTURE_HEIGHT", 640, int)
    cmd.extend(["--width", str(width), "--height", str(height)])
    proc: subprocess.CompletedProcess[bytes] = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        print(f"Capture failed: rc={proc.returncode}", file=sys.stderr)
        return ""
    return base64.b64encode(proc.stdout).decode("ascii")


def _subprocess_cursor_pos(brain: Any) -> tuple[int, int]:
    default_pos: int = franz.cfg(brain, "DEFAULT_CURSOR_POS", 500, int)
    cmd: list[str] = [sys.executable, str(WIN32_PATH), "cursor_pos"]
    region: str = franz.cfg(brain, "CAPTURE_REGION", "")
    if region:
        cmd.extend(["--region", region])
    proc: subprocess.CompletedProcess[bytes] = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return default_pos, default_pos
    parts: list[str] = proc.stdout.decode("ascii").strip().split(",")
    if len(parts) != 2:
        return default_pos, default_pos
    return int(parts[0]), int(parts[1])


def _action_xy_str(action: dict[str, Any], brain: Any) -> str:
    default_pos: int = franz.cfg(brain, "DEFAULT_CURSOR_POS", 500, int)
    return f"{int(action.get('x', default_pos))},{int(action.get('y', default_pos))}"


def _subprocess_execute_one(action: dict[str, Any], brain: Any) -> None:
    action_type: str = str(action.get("type", ""))
    params_str: str = str(action.get("params", ""))
    region: str = franz.cfg(brain, "CAPTURE_REGION", "")
    cmd: list[str] = [sys.executable, str(WIN32_PATH)]
    match action_type:
        case "click":
            cmd.extend(["click", "--pos", _action_xy_str(action, brain)])
        case "double_click":
            cmd.extend(["double_click", "--pos", _action_xy_str(action, brain)])
        case "right_click":
            cmd.extend(["right_click", "--pos", _action_xy_str(action, brain)])
        case "type_text":
            cmd.extend(["type_text", "--text", params_str])
        case "press_key":
            cmd.extend(["press_key", "--key", params_str])
        case "hotkey":
            cmd.extend(["hotkey", "--keys", params_str])
        case "scroll_up":
            cmd.extend(["scroll_up", "--pos", _action_xy_str(action, brain)])
        case "scroll_down":
            cmd.extend(["scroll_down", "--pos", _action_xy_str(action, brain)])
        case _:
            print(f"Unknown action type: {action_type}", file=sys.stderr)
            return
    if region:
        cmd.extend(["--region", region])
    result: subprocess.CompletedProcess[bytes] = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"Action {action_type} failed: rc={result.returncode}", file=sys.stderr)


def _subprocess_execute_drag(from_action: dict[str, Any], to_action: dict[str, Any], brain: Any) -> None:
    cmd: list[str] = [
        sys.executable, str(WIN32_PATH), "drag",
        "--from_pos", _action_xy_str(from_action, brain),
        "--to_pos", _action_xy_str(to_action, brain),
    ]
    region: str = franz.cfg(brain, "CAPTURE_REGION", "")
    if region:
        cmd.extend(["--region", region])
    subprocess.run(cmd, capture_output=True)


def _emit_swarm(agent: str, direction: str, text: str, image_b64: str = "", system: str = "") -> None:
    franz.swarm_message(agent, direction, text, image_b64, system)


def call_vlm(image_b64: str, user_text: str, system_prompt: str, brain: Any) -> str:
    vlm_request_delay: float = franz.cfg(brain, "VLM_REQUEST_DELAY", 0.0, float)
    if vlm_request_delay > 0:
        time.sleep(vlm_request_delay)

    _emit_swarm("VISIONARY", "input", user_text, image_b64, system_prompt)
    effective_text: str = user_text if user_text else "Observe the screenshot and describe what you see."
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": effective_text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ]
    body: bytes = json.dumps({
        "model": franz.cfg(brain, "VLM_MODEL_NAME", ""),
        "temperature": franz.cfg(brain, "VLM_TEMPERATURE", 0.6, float),
        "top_p": franz.cfg(brain, "VLM_TOP_P", 0.85, float),
        "max_tokens": franz.cfg(brain, "VLM_MAX_TOKENS", 800, int),
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }).encode("utf-8")
    endpoint: str = franz.cfg(brain, "VLM_ENDPOINT_URL", "")
    vlm_timeout: int = franz.cfg(brain, "VLM_TIMEOUT", 360, int)
    req: urllib.request.Request = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=vlm_timeout) as resp:
            resp_obj = json.loads(resp.read().decode("utf-8"))
        choices = resp_obj.get("choices", [])
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message", {})
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    _emit_swarm("VISIONARY", "output", content)
                    return content
        error_info: Any = resp_obj.get("error", {})
        if error_info:
            print(f"VLM server error in response body: {error_info}", file=sys.stderr)
        else:
            print(f"VLM returned unexpected structure: {str(resp_obj)[:500]}", file=sys.stderr)
    except Exception as exc:
        _emit_swarm("VISIONARY", "error", str(exc))
        print(f"VLM error: {exc}", file=sys.stderr)
    return ""


def _make_cursor_overlay(cx: int, cy: int, brain: Any) -> dict[str, Any]:
    arm: int = franz.cfg(brain, "CURSOR_ARM", 12, int)
    color: str = franz.cfg(brain, "CURSOR_COLOR", "#00ff00")
    font_size: int = franz.cfg(brain, "CURSOR_FONT_SIZE", 11, int)
    label_bg: str = franz.cfg(brain, "CURSOR_LABEL_BG", "#000000")
    show_label: bool = franz.cfg(brain, "SHOW_CURSOR", True, bool)
    label_offset: int = franz.cfg(brain, "CURSOR_LABEL_OFFSET", 18, int)
    label_limit: int = franz.cfg(brain, "CURSOR_LABEL_LIMIT", 980, int)
    label_text: str = f"[{cx},{cy}]" if show_label else ""
    return {
        "points": [
            [cx - arm, cy], [cx + arm, cy],
            [cx, cy], [cx, cy - arm], [cx, cy + arm],
        ],
        "closed": False,
        "stroke": color,
        "fill": "",
        "label": label_text,
        "label_position": [
            min(cx + label_offset, label_limit),
            min(cy + label_offset, label_limit),
        ],
        "label_style": {
            "font_size": font_size,
            "bg": label_bg,
            "color": color,
            "align": "left",
        },
    }


def _wait_for_annotation(raw_b64: str, frame_overlays: list[dict[str, Any]]) -> str:
    with _STATE.lock:
        _STATE.phase = "annotating"
        _STATE.raw_b64 = raw_b64
        _STATE.raw_seq += 1
        _STATE.overlays = frame_overlays
        _STATE.pending_seq = _STATE.raw_seq
        _STATE.annotated_seq = -1
        _STATE.annotated_b64 = ""
        _STATE.annotated_ready.clear()
    _EVENT_BUS.publish("frame_ready", {"seq": _STATE.raw_seq})
    _publish_state()
    if not _STATE.annotated_ready.wait(timeout=300):
        print("WARNING: Annotation timeout after 300s. Using raw screenshot.", file=sys.stderr)
        return raw_b64
    with _STATE.lock:
        return _STATE.annotated_b64


def _drain_swarm_into_state() -> None:
    new_msgs: list[dict[str, Any]] = franz.drain_queue(franz.swarm_queue)
    if not new_msgs:
        return
    with _STATE.lock:
        for msg in new_msgs:
            msg["turn"] = _STATE.turn
        _STATE.swarm_messages.extend(new_msgs)
    for msg in new_msgs:
        _EVENT_BUS.publish("swarm", {
            "agent": msg.get("agent", ""),
            "direction": msg.get("direction", ""),
            "text": msg.get("text", ""),
            "has_image": bool(msg.get("image_b64", "")),
            "system": msg.get("system", ""),
            "ts": msg.get("ts", 0),
            "turn": msg.get("turn", 0),
            "idx": len(_STATE.swarm_messages) - len(new_msgs) + new_msgs.index(msg),
        })


def _check_brain_annotation_request() -> None:
    try:
        req: dict[str, Any] = franz._brain_annotate_request.get_nowait()
    except Exception:
        return
    with _STATE.lock:
        _STATE.brain_ann_seq += 1
        _STATE.brain_ann_request = req
        _STATE.brain_ann_pending_seq = _STATE.brain_ann_seq
        _STATE.brain_ann_done_seq = -1
        _STATE.brain_ann_result_b64 = ""
        _STATE.brain_ann_live_b64 = ""
        _STATE.brain_ann_ready.clear()
    _EVENT_BUS.publish("brain_ann_ready", {"seq": _STATE.brain_ann_seq})


def engine_loop(brain: Any, session: SessionLog) -> None:
    system_prompt: str = str(getattr(brain, "SYSTEM_PROMPT", ""))
    on_vlm_response_fn = getattr(brain, "on_vlm_response")
    capture_delay: float = franz.cfg(brain, "CAPTURE_DELAY_SECONDS", 3.0, float)
    action_delay: float = franz.cfg(brain, "ACTION_DELAY_SECONDS", 0.3, float)
    show_cursor: bool = franz.cfg(brain, "SHOW_CURSOR", True, bool)
    default_pos: int = franz.cfg(brain, "DEFAULT_CURSOR_POS", 500, int)
    previous_user_text: str = ""
    last_cursor_pos: tuple[int, int] = (default_pos, default_pos)

    _BRIDGE.wait()

    if capture_delay > 0:
        time.sleep(capture_delay)
    raw_b64: str = _subprocess_capture(brain)
    if not raw_b64:
        print("ERROR: Initial capture failed", file=sys.stderr)
        return

    if show_cursor:
        last_cursor_pos = _subprocess_cursor_pos(brain)
    initial_overlays: list[dict[str, Any]] = []
    if show_cursor:
        initial_overlays.append(_make_cursor_overlay(*last_cursor_pos, brain))

    annotated_b64: str = _wait_for_annotation(raw_b64, initial_overlays)
    session.save_png(annotated_b64)

    while True:
        with _STATE.lock:
            _STATE.turn += 1
            current_turn: int = _STATE.turn
            _STATE.phase = "calling_vlm"
            swarm_start_idx: int = len(_STATE.swarm_messages)
        _publish_state()

        user_text_for_vlm: str = previous_user_text
        session.write_turn(current_turn, "INPUT", user_text_for_vlm)

        vlm_response: str = call_vlm(annotated_b64, user_text_for_vlm, system_prompt, brain)
        _drain_swarm_into_state()
        session.write_turn(current_turn, "OUTPUT", vlm_response)

        with _STATE.lock:
            _STATE.error_text = ""
            _STATE.phase = "parsing"
        _publish_state()

        user_text_out: str = on_vlm_response_fn(vlm_response)

        _drain_swarm_into_state()

        with _STATE.lock:
            turn_swarm: list[dict[str, Any]] = _STATE.swarm_messages[swarm_start_idx:]
        session.write_swarm(current_turn, turn_swarm)

        pipe_actions: list[dict[str, Any]] = franz.drain_queue(franz.action_queue)
        pipe_overlays: list[dict[str, Any]] = franz.drain_queue(franz.overlay_queue)

        with _STATE.lock:
            _STATE.display_text = vlm_response
            _STATE.display_actions = list(pipe_actions)
            _STATE.phase = "executing"
        _publish_state()

        executed_count: int = 0
        drag_count: int = 0
        pending_drag: dict[str, Any] | None = None
        for action in pipe_actions:
            action_type: str = str(action.get("type", ""))
            if action_type == "drag_start":
                pending_drag = action
                continue
            if action_type == "drag_end" and pending_drag is not None:
                _subprocess_execute_drag(pending_drag, action, brain)
                pending_drag = None
                drag_count += 1
                last_cursor_pos = _subprocess_cursor_pos(brain)
                continue
            if executed_count > 0:
                time.sleep(action_delay)
            _subprocess_execute_one(action, brain)
            executed_count += 1
            last_cursor_pos = _subprocess_cursor_pos(brain)

        total_actions: int = executed_count + drag_count
        print(f"Turn {current_turn}: {total_actions} actions ({drag_count} drags, {executed_count} other)")

        with _STATE.lock:
            _STATE.phase = "capturing"
        _publish_state()

        if capture_delay > 0:
            time.sleep(capture_delay)
        raw_b64 = _subprocess_capture(brain)
        if not raw_b64:
            print("WARN: Post-action capture failed, retrying", file=sys.stderr)
            time.sleep(1.0)
            raw_b64 = _subprocess_capture(brain)
            if not raw_b64:
                print("WARN: Capture retry also failed, using previous", file=sys.stderr)
                continue

        if show_cursor:
            last_cursor_pos = _subprocess_cursor_pos(brain)
        final_overlays: list[dict[str, Any]] = list(pipe_overlays)
        if show_cursor:
            final_overlays.append(_make_cursor_overlay(*last_cursor_pos, brain))

        annotated_b64 = _wait_for_annotation(raw_b64, final_overlays)
        session.save_png(annotated_b64)
        previous_user_text = user_text_out


class FranzHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format_str: str, *args: object) -> None:
        pass

    def _send_json(self, code: int, data: dict[str, Any]) -> None:
        body: bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _send_html(self, code: int, html_bytes: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(html_bytes)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _handle_sse(self) -> None:
        _BRIDGE.signal()
        sub: dict[str, Any] = _EVENT_BUS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b"event: connected\ndata: {}\n\n")
            self.wfile.flush()
            while sub["active"]:
                try:
                    payload: dict[str, Any] | None = sub["queue"].get(timeout=25)
                except Exception:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        break
                    continue
                if payload is None:
                    break
                event_type: str = payload.get("event", "message")
                event_data: str = json.dumps(payload.get("data", {}), ensure_ascii=False)
                chunk: bytes = f"event: {event_type}\ndata: {event_data}\n\n".encode("utf-8")
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    break
        finally:
            _EVENT_BUS.unsubscribe(sub)

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        match path:
            case "/" | "/index.html":
                self._send_html(200, PANEL_PATH.read_bytes())
            case "/events":
                self._handle_sse()
            case "/state":
                _BRIDGE.signal()
                _drain_swarm_into_state()
                _check_brain_annotation_request()
                with _STATE.lock:
                    self._send_json(200, {
                        "phase": _STATE.phase,
                        "turn": _STATE.turn,
                        "pending_seq": _STATE.pending_seq,
                        "annotated_seq": _STATE.annotated_seq,
                        "raw_seq": _STATE.raw_seq,
                        "error": _STATE.error_text,
                        "display": {
                            "text": _STATE.display_text,
                            "actions": _STATE.display_actions,
                        },
                        "msg_id": _STATE.turn,
                        "swarm_count": len(_STATE.swarm_messages),
                        "brain_ann_seq": _STATE.brain_ann_pending_seq,
                        "brain_ann_done": _STATE.brain_ann_done_seq,
                        "brain_ann_live": _STATE.brain_ann_live_b64[:20] if _STATE.brain_ann_live_b64 else "",
                    })
            case "/frame":
                with _STATE.lock:
                    self._send_json(200, {
                        "seq": _STATE.pending_seq,
                        "raw_b64": _STATE.raw_b64,
                        "overlays": _STATE.overlays,
                    })
            case "/brain_frame":
                with _STATE.lock:
                    if _STATE.brain_ann_request and _STATE.brain_ann_pending_seq > _STATE.brain_ann_done_seq:
                        self._send_json(200, {
                            "seq": _STATE.brain_ann_pending_seq,
                            "raw_b64": _STATE.brain_ann_request.get("image_b64", ""),
                            "overlays": _STATE.brain_ann_request.get("overlays", []),
                        })
                    else:
                        self._send_json(200, {"seq": 0, "raw_b64": "", "overlays": []})
            case "/swarm":
                _drain_swarm_into_state()
                after: int = 0
                qs: str = self.path.split("?", 1)[1] if "?" in self.path else ""
                for param in qs.split("&"):
                    if param.startswith("after="):
                        try:
                            after = int(param[6:])
                        except ValueError:
                            pass
                with _STATE.lock:
                    msgs: list[dict[str, Any]] = _STATE.swarm_messages[after:]
                    total: int = len(_STATE.swarm_messages)
                    strip_msgs: list[dict[str, Any]] = [
                        {
                            "agent": m.get("agent", ""),
                            "direction": m.get("direction", ""),
                            "text": m.get("text", ""),
                            "has_image": bool(m.get("image_b64", "")),
                            "system": m.get("system", ""),
                            "ts": m.get("ts", 0),
                            "turn": m.get("turn", 0),
                        }
                        for m in msgs
                    ]
                self._send_json(200, {"messages": strip_msgs, "total": total})
            case _ if path.startswith("/swarm_image/"):
                _drain_swarm_into_state()
                try:
                    idx: int = int(path.split("/")[2])
                except (IndexError, ValueError):
                    self._send_json(404, {"error": "bad index"})
                    return
                with _STATE.lock:
                    img: str = (
                        _STATE.swarm_messages[idx].get("image_b64", "")
                        if 0 <= idx < len(_STATE.swarm_messages)
                        else ""
                    )
                if img:
                    img_bytes: bytes = base64.b64decode(img)
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(img_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(img_bytes)
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        pass
                else:
                    self._send_json(404, {"error": "no image"})
            case _:
                self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path: str = self.path.split("?", 1)[0]
        content_length: int = int(self.headers.get("Content-Length", "0"))
        body: bytes = self.rfile.read(content_length) if content_length > 0 else b""
        match path:
            case "/annotated":
                try:
                    parsed: Any = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                if not isinstance(parsed, dict):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                seq_val: Any = parsed.get("seq")
                img_val: Any = parsed.get("image_b64", "")
                with _STATE.lock:
                    expected: int = _STATE.pending_seq
                if seq_val != expected:
                    self._send_json(409, {"ok": False, "err": f"seq mismatch got={seq_val} want={expected}"})
                    return
                if not isinstance(img_val, str) or len(img_val) < MIN_ANNOTATION_LENGTH:
                    self._send_json(400, {"ok": False, "err": "image too short"})
                    return
                with _STATE.lock:
                    _STATE.annotated_b64 = img_val
                    _STATE.annotated_seq = expected
                _STATE.annotated_ready.set()
                _EVENT_BUS.publish("annotation_done", {"seq": expected})
                self._send_json(200, {"ok": True, "seq": expected})
            case "/brain_annotated":
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                if not isinstance(parsed, dict):
                    self._send_json(400, {"ok": False, "err": "bad json"})
                    return
                seq_val = parsed.get("seq")
                img_val = parsed.get("image_b64", "")
                with _STATE.lock:
                    expected = _STATE.brain_ann_pending_seq
                if seq_val != expected:
                    self._send_json(409, {"ok": False, "err": f"brain ann seq mismatch got={seq_val} want={expected}"})
                    return
                if not isinstance(img_val, str) or len(img_val) < MIN_ANNOTATION_LENGTH:
                    self._send_json(400, {"ok": False, "err": "image too short"})
                    return
                with _STATE.lock:
                    _STATE.brain_ann_result_b64 = img_val
                    _STATE.brain_ann_done_seq = expected
                    _STATE.brain_ann_request = None
                    _STATE.brain_ann_live_b64 = img_val
                franz._brain_annotate_result.put(img_val)
                _EVENT_BUS.publish("brain_ann_done", {"seq": expected})
                self._send_json(200, {"ok": True, "seq": expected})
            case _:
                self._send_json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()