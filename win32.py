import ctypes
import ctypes.wintypes as W
import struct
import sys
import time
import zlib
from dataclasses import dataclass


@dataclass(slots=True)
class Win32Config:
    drag_step_count: int = 25
    drag_step_delay: float = 0.008
    default_capture_width: int = 640
    default_capture_height: int = 640
    click_settle_delay: float = 0.03
    key_settle_delay: float = 0.03
    type_inter_key_delay: float = 0.02
    type_down_delay: float = 0.01
    hotkey_inter_delay: float = 0.02
    scroll_click_delay: float = 0.03
    scroll_default_clicks: int = 3
    double_click_inter: float = 0.05
    overlay_alpha: int = 90
    selector_min_size: int = 5


NORM: int = 1000
SRCCOPY: int = 0x00CC0020
CAPTUREBLT: int = 0x40000000
HALFTONE: int = 4
LEFT_DOWN: int = 0x0002
LEFT_UP: int = 0x0004
RIGHT_DOWN: int = 0x0008
RIGHT_UP: int = 0x0010
MOUSE_WHEEL: int = 0x0800
WHEEL_DELTA: int = 120
KEYEVENTF_KEYUP: int = 0x0002
KEYEVENTF_EXTENDED: int = 0x0001

EXTENDED_VKS: frozenset[int] = frozenset(
    {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}
)

VK_MAP: dict[str, int] = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "windows": 0x5B, "space": 0x20,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
}
for _i in range(26):
    VK_MAP[chr(ord("a") + _i)] = ord("A") + _i
for _i in range(10):
    VK_MAP[chr(ord("0") + _i)] = ord("0") + _i

WS_EX_LAYERED: int = 0x00080000
WS_EX_TOPMOST: int = 0x00000008
WS_EX_TOOLWINDOW: int = 0x00000080
WS_POPUP: int = 0x80000000
WS_VISIBLE: int = 0x10000000
LWA_ALPHA: int = 0x00000002
WM_PAINT: int = 0x000F
WM_ERASEBKGND: int = 0x0014
WM_LBUTTONDOWN: int = 0x0201
WM_LBUTTONUP: int = 0x0202
WM_MOUSEMOVE: int = 0x0200
WM_RBUTTONDOWN: int = 0x0204
WM_KEYDOWN: int = 0x0100
WM_CLOSE: int = 0x0010
WM_DESTROY: int = 0x0002
VK_ESCAPE: int = 0x1B
IDC_CROSS: int = 32515
CS_HREDRAW: int = 0x0002
CS_VREDRAW: int = 0x0001
SM_CXSCREEN: int = 0
SM_CYSCREEN: int = 1
PS_SOLID: int = 0
PS_DASH: int = 1
TRANSPARENT_BK: int = 1
NULL_BRUSH: int = 5

EXIT_OK: int = 0
EXIT_CANCEL: int = 2

LRESULT = ctypes.c_ssize_t
WNDPROC_TYPE = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

HCURSOR = W.HANDLE
HICON = W.HANDLE
HMODULE = W.HANDLE

ctypes.WinDLL("shcore", use_last_error=True).SetProcessDpiAwareness(2)

_user32: ctypes.WinDLL = ctypes.WinDLL("user32", use_last_error=True)
_gdi32: ctypes.WinDLL = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32: ctypes.WinDLL = ctypes.WinDLL("kernel32", use_last_error=True)


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
        ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
        ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
        ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD),
        ("biClrImportant", W.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", W.DWORD * 3)]


class _PaintStruct(ctypes.Structure):
    _fields_ = [
        ("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT),
        ("fRestore", W.BOOL), ("fIncUpdate", W.BOOL),
        ("rgbReserved", W.BYTE * 32),
    ]


class _WndClassExW(ctypes.Structure):
    _fields_ = [
        ("cbSize", W.UINT), ("style", W.UINT), ("lpfnWndProc", WNDPROC_TYPE),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", W.HINSTANCE), ("hIcon", HICON), ("hCursor", HCURSOR),
        ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR),
        ("lpszClassName", W.LPCWSTR), ("hIconSm", HICON),
    ]


def _setup_bindings() -> None:
    _user32.GetDC.argtypes = [W.HWND]
    _user32.GetDC.restype = W.HDC
    _user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
    _user32.ReleaseDC.restype = ctypes.c_int
    _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _gdi32.CreateCompatibleDC.argtypes = [W.HDC]
    _gdi32.CreateCompatibleDC.restype = W.HDC
    _gdi32.CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
    _gdi32.CreateDIBSection.restype = W.HBITMAP
    _gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
    _gdi32.SelectObject.restype = W.HGDIOBJ
    _gdi32.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.BitBlt.restype = W.BOOL
    _gdi32.StretchBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.StretchBlt.restype = W.BOOL
    _gdi32.SetStretchBltMode.argtypes = [W.HDC, ctypes.c_int]
    _gdi32.SetStretchBltMode.restype = ctypes.c_int
    _gdi32.SetBrushOrgEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    _gdi32.SetBrushOrgEx.restype = W.BOOL
    _gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
    _gdi32.DeleteObject.restype = W.BOOL
    _gdi32.DeleteDC.argtypes = [W.HDC]
    _gdi32.DeleteDC.restype = W.BOOL
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = W.BOOL
    _user32.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, ctypes.c_long, ctypes.c_ulong]
    _user32.mouse_event.restype = None
    _user32.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.POINTER(ctypes.c_ulong)]
    _user32.keybd_event.restype = None
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(W.POINT)]
    _user32.GetCursorPos.restype = W.BOOL
    _kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = HMODULE
    _user32.LoadCursorW.argtypes = [W.HINSTANCE, W.LPCWSTR]
    _user32.LoadCursorW.restype = HCURSOR
    _user32.RegisterClassExW.argtypes = [ctypes.POINTER(_WndClassExW)]
    _user32.RegisterClassExW.restype = W.ATOM
    _user32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    _user32.CreateWindowExW.restype = W.HWND
    _user32.SetLayeredWindowAttributes.argtypes = [W.HWND, W.DWORD, W.BYTE, W.DWORD]
    _user32.SetLayeredWindowAttributes.restype = W.BOOL
    _user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    _user32.DefWindowProcW.restype = LRESULT
    _user32.BeginPaint.argtypes = [W.HWND, ctypes.POINTER(_PaintStruct)]
    _user32.BeginPaint.restype = W.HDC
    _user32.EndPaint.argtypes = [W.HWND, ctypes.POINTER(_PaintStruct)]
    _user32.EndPaint.restype = W.BOOL
    _user32.InvalidateRect.argtypes = [W.HWND, ctypes.c_void_p, W.BOOL]
    _user32.InvalidateRect.restype = W.BOOL
    _user32.DestroyWindow.argtypes = [W.HWND]
    _user32.DestroyWindow.restype = W.BOOL
    _user32.PostQuitMessage.argtypes = [ctypes.c_int]
    _user32.PostQuitMessage.restype = None
    _user32.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
    _user32.TranslateMessage.restype = W.BOOL
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
    _user32.DispatchMessageW.restype = LRESULT
    _user32.SetCapture.argtypes = [W.HWND]
    _user32.SetCapture.restype = W.HWND
    _user32.ReleaseCapture.argtypes = []
    _user32.ReleaseCapture.restype = W.BOOL
    _user32.SetForegroundWindow.argtypes = [W.HWND]
    _user32.SetForegroundWindow.restype = W.BOOL
    _user32.SetFocus.argtypes = [W.HWND]
    _user32.SetFocus.restype = W.HWND
    _user32.FillRect.argtypes = [W.HDC, ctypes.POINTER(W.RECT), W.HBRUSH]
    _user32.FillRect.restype = W.BOOL
    _gdi32.CreateSolidBrush.argtypes = [W.DWORD]
    _gdi32.CreateSolidBrush.restype = W.HBRUSH
    _gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, W.DWORD]
    _gdi32.CreatePen.restype = W.HGDIOBJ
    _gdi32.Rectangle.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _gdi32.Rectangle.restype = W.BOOL
    _gdi32.SetBkMode.argtypes = [W.HDC, ctypes.c_int]
    _gdi32.SetBkMode.restype = ctypes.c_int
    _gdi32.GetStockObject.argtypes = [ctypes.c_int]
    _gdi32.GetStockObject.restype = W.HGDIOBJ


_setup_bindings()

CONFIG: Win32Config = Win32Config()


def _clamp_norm(value: int) -> int:
    return max(0, min(NORM, value))


def _screen_size() -> tuple[int, int]:
    return int(_user32.GetSystemMetrics(SM_CXSCREEN)), int(_user32.GetSystemMetrics(SM_CYSCREEN))


def _make_bmi(width: int, height: int) -> _BitmapInfo:
    info: _BitmapInfo = _BitmapInfo()
    header: _BitmapInfoHeader = info.bmiHeader
    header.biSize = ctypes.sizeof(_BitmapInfoHeader)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0
    return info


def _create_dib(device_context: int, width: int, height: int) -> tuple[int, int]:
    bits_ptr: ctypes.c_void_p = ctypes.c_void_p()
    bmi: _BitmapInfo = _make_bmi(width, height)
    bitmap_handle: int = _gdi32.CreateDIBSection(
        device_context, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0
    )
    if bitmap_handle and bits_ptr.value:
        return bitmap_handle, int(bits_ptr.value)
    return 0, 0


def _capture_full_screen() -> tuple[bytes, int, int] | None:
    screen_w, screen_h = _screen_size()
    screen_dc: int = _user32.GetDC(0)
    if not screen_dc:
        return None
    mem_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    if not mem_dc:
        _user32.ReleaseDC(0, screen_dc)
        return None
    bitmap_handle, bits_addr = _create_dib(screen_dc, screen_w, screen_h)
    if not bitmap_handle:
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(0, screen_dc)
        return None
    old_obj: int = _gdi32.SelectObject(mem_dc, bitmap_handle)
    _gdi32.BitBlt(mem_dc, 0, 0, screen_w, screen_h, screen_dc, 0, 0, SRCCOPY | CAPTUREBLT)
    raw_bytes: bytes = bytes(
        (ctypes.c_ubyte * (screen_w * screen_h * 4)).from_address(bits_addr)
    )
    _gdi32.SelectObject(mem_dc, old_obj)
    _gdi32.DeleteObject(bitmap_handle)
    _gdi32.DeleteDC(mem_dc)
    _user32.ReleaseDC(0, screen_dc)
    return raw_bytes, screen_w, screen_h


def _parse_region(region_str: str) -> tuple[int, int, int, int]:
    parts: list[str] = region_str.split(",")
    if len(parts) != 4:
        raise ValueError(f"region must be x1,y1,x2,y2 got: {region_str}")
    return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])


def _parse_pos(pos_str: str) -> tuple[int, int]:
    parts: list[str] = pos_str.split(",")
    if len(parts) != 2:
        raise ValueError(f"pos must be x,y got: {pos_str}")
    return int(parts[0]), int(parts[1])


def _norm_region_to_pixels(
    norm_x1: int, norm_y1: int, norm_x2: int, norm_y2: int,
    base_w: int, base_h: int,
) -> tuple[int, int, int, int]:
    x1_val: int = _clamp_norm(norm_x1)
    y1_val: int = _clamp_norm(norm_y1)
    x2_val: int = _clamp_norm(norm_x2)
    y2_val: int = _clamp_norm(norm_y2)
    if x2_val < x1_val:
        x1_val, x2_val = x2_val, x1_val
    if y2_val < y1_val:
        y1_val, y2_val = y2_val, y1_val
    px_x1: int = max(0, min(base_w, (x1_val * base_w + NORM // 2) // NORM))
    px_y1: int = max(0, min(base_h, (y1_val * base_h + NORM // 2) // NORM))
    px_x2: int = max(0, min(base_w, (x2_val * base_w + NORM // 2) // NORM))
    px_y2: int = max(0, min(base_h, (y2_val * base_h + NORM // 2) // NORM))
    return px_x1, px_y1, px_x2, px_y2


def _norm_to_screen_pixel(
    norm_x: int, norm_y: int,
    region_x1: int, region_y1: int, region_x2: int, region_y2: int,
) -> tuple[int, int]:
    screen_w, screen_h = _screen_size()
    px_x1, px_y1, px_x2, px_y2 = _norm_region_to_pixels(
        region_x1, region_y1, region_x2, region_y2, screen_w, screen_h
    )
    crop_w: int = max(1, px_x2 - px_x1)
    crop_h: int = max(1, px_y2 - px_y1)
    clamped_x: int = _clamp_norm(norm_x)
    clamped_y: int = _clamp_norm(norm_y)
    pixel_x: int = px_x1 + (clamped_x * (crop_w - 1) + NORM // 2) // NORM if crop_w > 1 else px_x1
    pixel_y: int = px_y1 + (clamped_y * (crop_h - 1) + NORM // 2) // NORM if crop_h > 1 else px_y1
    return pixel_x, pixel_y


def _screen_pixel_to_norm(
    pixel_x: int, pixel_y: int,
    region_x1: int, region_y1: int, region_x2: int, region_y2: int,
) -> tuple[int, int]:
    screen_w, screen_h = _screen_size()
    px_x1, px_y1, px_x2, px_y2 = _norm_region_to_pixels(
        region_x1, region_y1, region_x2, region_y2, screen_w, screen_h
    )
    crop_w: int = max(1, px_x2 - px_x1)
    crop_h: int = max(1, px_y2 - px_y1)
    rel_x: int = pixel_x - px_x1
    rel_y: int = pixel_y - px_y1
    norm_x: int = _clamp_norm((rel_x * NORM + crop_w // 2) // crop_w) if crop_w > 1 else 500
    norm_y: int = _clamp_norm((rel_y * NORM + crop_h // 2) // crop_h) if crop_h > 1 else 500
    return norm_x, norm_y


def _crop_bgra(
    bgra: bytes, src_w: int, src_h: int,
    crop_x1: int, crop_y1: int, crop_x2: int, crop_y2: int,
) -> tuple[bytes, int, int]:
    crop_w: int = crop_x2 - crop_x1
    crop_h: int = crop_y2 - crop_y1
    if crop_w <= 0 or crop_h <= 0:
        return bgra, src_w, src_h
    source: memoryview = memoryview(bgra)
    output: bytearray = bytearray(crop_w * crop_h * 4)
    src_stride: int = src_w * 4
    dst_stride: int = crop_w * 4
    for yidx in range(crop_h):
        src_offset: int = (crop_y1 + yidx) * src_stride + crop_x1 * 4
        dst_offset: int = yidx * dst_stride
        output[dst_offset:dst_offset + dst_stride] = source[src_offset:src_offset + dst_stride]
    return bytes(output), crop_w, crop_h


def _stretch_bgra(
    bgra: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int,
) -> bytes | None:
    screen_dc: int = _user32.GetDC(0)
    if not screen_dc:
        return None
    src_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    dst_dc: int = _gdi32.CreateCompatibleDC(screen_dc)
    if not src_dc or not dst_dc:
        if src_dc:
            _gdi32.DeleteDC(src_dc)
        if dst_dc:
            _gdi32.DeleteDC(dst_dc)
        _user32.ReleaseDC(0, screen_dc)
        return None
    src_bmp, src_bits = _create_dib(screen_dc, src_w, src_h)
    if not src_bmp:
        _gdi32.DeleteDC(src_dc)
        _gdi32.DeleteDC(dst_dc)
        _user32.ReleaseDC(0, screen_dc)
        return None
    ctypes.memmove(src_bits, bgra, src_w * src_h * 4)
    old_src: int = _gdi32.SelectObject(src_dc, src_bmp)
    dst_bmp, dst_bits = _create_dib(screen_dc, dst_w, dst_h)
    if not dst_bmp:
        _gdi32.SelectObject(src_dc, old_src)
        _gdi32.DeleteObject(src_bmp)
        _gdi32.DeleteDC(src_dc)
        _gdi32.DeleteDC(dst_dc)
        _user32.ReleaseDC(0, screen_dc)
        return None
    old_dst: int = _gdi32.SelectObject(dst_dc, dst_bmp)
    _gdi32.SetStretchBltMode(dst_dc, HALFTONE)
    _gdi32.SetBrushOrgEx(dst_dc, 0, 0, None)
    _gdi32.StretchBlt(dst_dc, 0, 0, dst_w, dst_h, src_dc, 0, 0, src_w, src_h, SRCCOPY)
    result: bytes = bytes(
        (ctypes.c_ubyte * (dst_w * dst_h * 4)).from_address(dst_bits)
    )
    _gdi32.SelectObject(dst_dc, old_dst)
    _gdi32.SelectObject(src_dc, old_src)
    _gdi32.DeleteObject(dst_bmp)
    _gdi32.DeleteObject(src_bmp)
    _gdi32.DeleteDC(dst_dc)
    _gdi32.DeleteDC(src_dc)
    _user32.ReleaseDC(0, screen_dc)
    return result


def _bgra_to_png(bgra: bytes, width: int, height: int) -> bytes:
    stride: int = width * 4
    source: memoryview = memoryview(bgra)
    rows: bytearray = bytearray()
    for yidx in range(height):
        rows.append(0)
        row: memoryview = source[yidx * stride:(yidx + 1) * stride]
        for xoff in range(0, len(row), 4):
            rows.extend((row[xoff + 2], row[xoff + 1], row[xoff + 0], 255))

    def make_chunk(chunk_type: bytes, chunk_data: bytes) -> bytes:
        combined: bytes = chunk_type + chunk_data
        return (
            struct.pack(">I", len(chunk_data))
            + combined
            + struct.pack(">I", zlib.crc32(combined) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + make_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + make_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + make_chunk(b"IEND", b"")
    )


def _do_capture(region_str: str, width: int, height: int) -> bytes:
    captured: tuple[bytes, int, int] | None = _capture_full_screen()
    if captured is None:
        return b""
    bgra: bytes = captured[0]
    src_w: int = captured[1]
    src_h: int = captured[2]
    if region_str:
        norm_x1, norm_y1, norm_x2, norm_y2 = _parse_region(region_str)
        px_x1, px_y1, px_x2, px_y2 = _norm_region_to_pixels(
            norm_x1, norm_y1, norm_x2, norm_y2, src_w, src_h
        )
        bgra, src_w, src_h = _crop_bgra(bgra, src_w, src_h, px_x1, px_y1, px_x2, px_y2)
    if width > 0 and height > 0 and (src_w, src_h) != (width, height):
        stretched: bytes | None = _stretch_bgra(bgra, src_w, src_h, width, height)
        if stretched is not None:
            bgra = stretched
            src_w = width
            src_h = height
    return _bgra_to_png(bgra, src_w, src_h)


def _resolve_screen_pos(norm_x: int, norm_y: int, region_str: str) -> tuple[int, int]:
    if region_str:
        rx1, ry1, rx2, ry2 = _parse_region(region_str)
    else:
        rx1, ry1, rx2, ry2 = 0, 0, NORM, NORM
    return _norm_to_screen_pixel(norm_x, norm_y, rx1, ry1, rx2, ry2)


def _move_cursor(pixel_x: int, pixel_y: int) -> None:
    _user32.SetCursorPos(pixel_x, pixel_y)


def _mouse_event(flags: int, data: int = 0) -> None:
    _user32.mouse_event(flags, 0, 0, data, 0)


def _key_event(vk_code: int, is_up: bool = False) -> None:
    flags: int = 0
    if is_up:
        flags |= KEYEVENTF_KEYUP
    if vk_code in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDED
    _user32.keybd_event(vk_code, 0, flags, None)


def _do_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_DOWN)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_UP)


def _do_double_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_DOWN)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_UP)
    time.sleep(CONFIG.double_click_inter)
    _mouse_event(LEFT_DOWN)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_UP)


def _do_right_click(pos_str: str, region_str: str) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(RIGHT_DOWN)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(RIGHT_UP)


def _do_type_text(text: str) -> None:
    for char in text:
        vk_scan: int = ctypes.windll.user32.VkKeyScanW(ord(char))
        if vk_scan == -1:
            continue
        vk_code: int = vk_scan & 0xFF
        need_shift: bool = bool(vk_scan & 0x100)
        need_ctrl: bool = bool(vk_scan & 0x200)
        need_alt: bool = bool(vk_scan & 0x400)
        if need_ctrl:
            _key_event(0x11)
        if need_alt:
            _key_event(0x12)
        if need_shift:
            _key_event(0x10)
        _key_event(vk_code)
        time.sleep(CONFIG.type_down_delay)
        _key_event(vk_code, True)
        if need_shift:
            _key_event(0x10, True)
        if need_alt:
            _key_event(0x12, True)
        if need_ctrl:
            _key_event(0x11, True)
        time.sleep(CONFIG.type_inter_key_delay)


def _do_press_key(key_name: str) -> None:
    lower_name: str = key_name.strip().lower()
    vk_code: int | None = VK_MAP.get(lower_name)
    if vk_code is None:
        return
    _key_event(vk_code)
    time.sleep(CONFIG.key_settle_delay)
    _key_event(vk_code, True)


def _do_hotkey(keys_str: str) -> None:
    key_names: list[str] = []
    for part in keys_str.replace(",", "+").replace(" ", "+").split("+"):
        stripped: str = part.strip().lower()
        if stripped:
            key_names.append(stripped)
    vk_codes: list[int] = []
    for name in key_names:
        vk_code: int | None = VK_MAP.get(name)
        if vk_code is not None:
            vk_codes.append(vk_code)
        elif len(name) == 1:
            vk_scan: int = ctypes.windll.user32.VkKeyScanW(ord(name))
            if vk_scan != -1:
                vk_codes.append(vk_scan & 0xFF)
    for vk_code_val in vk_codes:
        _key_event(vk_code_val)
        time.sleep(CONFIG.hotkey_inter_delay)
    for vk_code_val in reversed(vk_codes):
        _key_event(vk_code_val, True)
        time.sleep(CONFIG.hotkey_inter_delay)


def _do_scroll(pos_str: str, region_str: str, direction: int, clicks: int) -> None:
    norm_x, norm_y = _parse_pos(pos_str)
    pixel_x, pixel_y = _resolve_screen_pos(norm_x, norm_y, region_str)
    _move_cursor(pixel_x, pixel_y)
    time.sleep(CONFIG.click_settle_delay)
    for _ in range(max(1, clicks)):
        _mouse_event(MOUSE_WHEEL, direction * WHEEL_DELTA)
        time.sleep(CONFIG.scroll_click_delay)


def _do_drag(from_pos_str: str, to_pos_str: str, region_str: str) -> None:
    from_nx, from_ny = _parse_pos(from_pos_str)
    to_nx, to_ny = _parse_pos(to_pos_str)
    from_x, from_y = _resolve_screen_pos(from_nx, from_ny, region_str)
    to_x, to_y = _resolve_screen_pos(to_nx, to_ny, region_str)
    steps: int = max(1, CONFIG.drag_step_count)
    _move_cursor(from_x, from_y)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_DOWN)
    time.sleep(CONFIG.click_settle_delay)
    for step_idx in range(1, steps + 1):
        interp_x: int = from_x + (to_x - from_x) * step_idx // steps
        interp_y: int = from_y + (to_y - from_y) * step_idx // steps
        _move_cursor(interp_x, interp_y)
        time.sleep(CONFIG.drag_step_delay)
    time.sleep(CONFIG.click_settle_delay)
    _mouse_event(LEFT_UP)


def _do_cursor_pos(region_str: str) -> str:
    point: W.POINT = W.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    if region_str:
        rx1, ry1, rx2, ry2 = _parse_region(region_str)
    else:
        rx1, ry1, rx2, ry2 = 0, 0, NORM, NORM
    norm_x, norm_y = _screen_pixel_to_norm(point.x, point.y, rx1, ry1, rx2, ry2)
    return f"{norm_x},{norm_y}"


_selector_dragging: bool = False
_selector_sx: int = 0
_selector_sy: int = 0
_selector_ex: int = 0
_selector_ey: int = 0
_selector_exit_code: int = EXIT_CANCEL
_selector_result: tuple[int, int, int, int] | None = None
_selector_wndproc_ref: WNDPROC_TYPE | None = None
_selector_screen_w: int = 0
_selector_screen_h: int = 0
_selector_null_brush: int = 0


def _selector_get_xy(lparam: int) -> tuple[int, int]:
    xval: int = lparam & 0xFFFF
    yval: int = (lparam >> 16) & 0xFFFF
    if xval > 32767:
        xval -= 65536
    if yval > 32767:
        yval -= 65536
    return xval, yval


def _selector_wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    global _selector_dragging, _selector_sx, _selector_sy
    global _selector_ex, _selector_ey, _selector_result, _selector_exit_code

    if msg == WM_ERASEBKGND:
        return 1
    if msg == WM_KEYDOWN:
        if int(wparam) == VK_ESCAPE:
            _selector_result = None
            _selector_exit_code = EXIT_CANCEL
            _user32.DestroyWindow(hwnd)
            return 0
    if msg == WM_RBUTTONDOWN:
        _selector_result = None
        _selector_exit_code = EXIT_OK
        _user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_CLOSE:
        _selector_result = None
        _selector_exit_code = EXIT_CANCEL
        _user32.DestroyWindow(hwnd)
        return 0
    if msg == WM_LBUTTONDOWN:
        _selector_sx, _selector_sy = _selector_get_xy(lparam)
        _selector_ex, _selector_ey = _selector_sx, _selector_sy
        _selector_dragging = True
        _user32.SetCapture(hwnd)
        _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == WM_MOUSEMOVE:
        if _selector_dragging:
            _selector_ex, _selector_ey = _selector_get_xy(lparam)
            _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == WM_LBUTTONUP:
        if _selector_dragging:
            _selector_ex, _selector_ey = _selector_get_xy(lparam)
            _selector_dragging = False
            _user32.ReleaseCapture()
            rect_x1: int = min(_selector_sx, _selector_ex)
            rect_y1: int = min(_selector_sy, _selector_ey)
            rect_x2: int = max(_selector_sx, _selector_ex)
            rect_y2: int = max(_selector_sy, _selector_ey)
            if (abs(rect_x2 - rect_x1) > CONFIG.selector_min_size
                    and abs(rect_y2 - rect_y1) > CONFIG.selector_min_size):
                _selector_result = (rect_x1, rect_y1, rect_x2, rect_y2)
                _selector_exit_code = EXIT_OK
                _user32.DestroyWindow(hwnd)
            else:
                _user32.InvalidateRect(hwnd, None, True)
        return 0
    if msg == WM_PAINT:
        paint_struct: _PaintStruct = _PaintStruct()
        hdc: int = _user32.BeginPaint(hwnd, ctypes.byref(paint_struct))
        brush_bg: int = _gdi32.CreateSolidBrush(0x00000000)
        rect_full: W.RECT = W.RECT(0, 0, _selector_screen_w, _selector_screen_h)
        _user32.FillRect(hdc, ctypes.byref(rect_full), brush_bg)
        _gdi32.DeleteObject(brush_bg)
        if _selector_dragging or (_selector_ex != _selector_sx or _selector_ey != _selector_sy):
            draw_x1: int = min(_selector_sx, _selector_ex)
            draw_y1: int = min(_selector_sy, _selector_ey)
            draw_x2: int = max(_selector_sx, _selector_ex)
            draw_y2: int = max(_selector_sy, _selector_ey)
            pen_white: int = _gdi32.CreatePen(PS_SOLID, 3, 0x00FFFFFF)
            pen_green: int = _gdi32.CreatePen(PS_DASH, 1, 0x0000FF00)
            old_pen: int = _gdi32.SelectObject(hdc, pen_white)
            old_brush: int = _gdi32.SelectObject(hdc, _selector_null_brush)
            _gdi32.SetBkMode(hdc, TRANSPARENT_BK)
            _gdi32.Rectangle(hdc, draw_x1, draw_y1, draw_x2, draw_y2)
            _gdi32.SelectObject(hdc, pen_green)
            _gdi32.Rectangle(hdc, draw_x1 - 2, draw_y1 - 2, draw_x2 + 2, draw_y2 + 2)
            _gdi32.SelectObject(hdc, old_pen)
            _gdi32.SelectObject(hdc, old_brush)
            _gdi32.DeleteObject(pen_white)
            _gdi32.DeleteObject(pen_green)
        _user32.EndPaint(hwnd, ctypes.byref(paint_struct))
        return 0
    if msg == WM_DESTROY:
        _user32.PostQuitMessage(0)
        return 0
    return int(_user32.DefWindowProcW(hwnd, msg, wparam, lparam))


def _do_select_region() -> tuple[str, int]:
    global _selector_wndproc_ref, _selector_result, _selector_exit_code
    global _selector_screen_w, _selector_screen_h, _selector_null_brush
    global _selector_dragging, _selector_sx, _selector_sy
    global _selector_ex, _selector_ey

    _selector_dragging = False
    _selector_sx = 0
    _selector_sy = 0
    _selector_ex = 0
    _selector_ey = 0
    _selector_result = None
    _selector_exit_code = EXIT_CANCEL

    _selector_screen_w, _selector_screen_h = _screen_size()

    _selector_null_brush = _gdi32.GetStockObject(NULL_BRUSH)

    hinst: int = _kernel32.GetModuleHandleW(None)
    class_name: str = "FranzSelectorWin32"
    _selector_wndproc_ref = WNDPROC_TYPE(_selector_wndproc)

    wnd_class: _WndClassExW = _WndClassExW()
    wnd_class.cbSize = ctypes.sizeof(_WndClassExW)
    wnd_class.style = CS_HREDRAW | CS_VREDRAW
    wnd_class.lpfnWndProc = _selector_wndproc_ref
    wnd_class.cbClsExtra = 0
    wnd_class.cbWndExtra = 0
    wnd_class.hInstance = hinst
    wnd_class.hIcon = 0
    wnd_class.hCursor = _user32.LoadCursorW(None, ctypes.cast(IDC_CROSS, W.LPCWSTR))
    wnd_class.hbrBackground = 0
    wnd_class.lpszMenuName = None
    wnd_class.lpszClassName = class_name
    wnd_class.hIconSm = 0

    atom: int = _user32.RegisterClassExW(ctypes.byref(wnd_class))
    if not atom:
        last_err: int = ctypes.get_last_error()
        if last_err != 1410:
            return "", EXIT_CANCEL

    ex_style: int = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
    hwnd: int = _user32.CreateWindowExW(
        ex_style, class_name, "Franz Region Select",
        WS_POPUP | WS_VISIBLE, 0, 0,
        _selector_screen_w, _selector_screen_h,
        None, None, hinst, None,
    )
    if not hwnd:
        return "", EXIT_CANCEL

    _user32.SetLayeredWindowAttributes(hwnd, 0, CONFIG.overlay_alpha, LWA_ALPHA)
    _user32.SetForegroundWindow(hwnd)
    _user32.SetFocus(hwnd)

    msg_struct: W.MSG = W.MSG()
    while True:
        result: int = _user32.GetMessageW(ctypes.byref(msg_struct), None, 0, 0)
        if result == -1 or result == 0:
            break
        _user32.TranslateMessage(ctypes.byref(msg_struct))
        _user32.DispatchMessageW(ctypes.byref(msg_struct))

    if _selector_result is not None:
        px_x1, px_y1, px_x2, px_y2 = _selector_result
        norm_x1: int = max(0, min(NORM, round(px_x1 * NORM / _selector_screen_w)))
        norm_y1: int = max(0, min(NORM, round(px_y1 * NORM / _selector_screen_h)))
        norm_x2: int = max(0, min(NORM, round(px_x2 * NORM / _selector_screen_w)))
        norm_y2: int = max(0, min(NORM, round(px_y2 * NORM / _selector_screen_h)))
        return f"{norm_x1},{norm_y1},{norm_x2},{norm_y2}", EXIT_OK
    return "", _selector_exit_code


def main() -> None:
    args: list[str] = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: python win32.py <command> [options]\n")
        sys.stderr.flush()
        raise SystemExit(1)

    command: str = args[0]

    def get_arg(name: str, default: str = "") -> str:
        flag: str = f"--{name}"
        for idx in range(len(args)):
            if args[idx] == flag and idx + 1 < len(args):
                return args[idx + 1]
        return default

    match command:
        case "capture":
            region_arg: str = get_arg("region", "")
            width_arg: int = int(get_arg("width", str(CONFIG.default_capture_width)))
            height_arg: int = int(get_arg("height", str(CONFIG.default_capture_height)))
            png_bytes: bytes = _do_capture(region_arg, width_arg, height_arg)
            sys.stdout.buffer.write(png_bytes)
            sys.stdout.buffer.flush()

        case "click":
            pos_arg: str = get_arg("pos", "500,500")
            region_val: str = get_arg("region", "")
            _do_click(pos_arg, region_val)

        case "double_click":
            pos_arg = get_arg("pos", "500,500")
            region_val = get_arg("region", "")
            _do_double_click(pos_arg, region_val)

        case "right_click":
            pos_arg = get_arg("pos", "500,500")
            region_val = get_arg("region", "")
            _do_right_click(pos_arg, region_val)

        case "type_text":
            text_arg: str = get_arg("text", "")
            _do_type_text(text_arg)

        case "press_key":
            key_arg: str = get_arg("key", "")
            _do_press_key(key_arg)

        case "hotkey":
            keys_arg: str = get_arg("keys", "")
            _do_hotkey(keys_arg)

        case "scroll_up":
            pos_arg = get_arg("pos", "500,500")
            region_val = get_arg("region", "")
            clicks_arg: int = int(get_arg("clicks", str(CONFIG.scroll_default_clicks)))
            _do_scroll(pos_arg, region_val, 1, clicks_arg)

        case "scroll_down":
            pos_arg = get_arg("pos", "500,500")
            region_val = get_arg("region", "")
            clicks_arg = int(get_arg("clicks", str(CONFIG.scroll_default_clicks)))
            _do_scroll(pos_arg, region_val, -1, clicks_arg)

        case "drag":
            from_pos_arg: str = get_arg("from_pos", "400,400")
            to_pos_arg: str = get_arg("to_pos", "600,600")
            region_val = get_arg("region", "")
            _do_drag(from_pos_arg, to_pos_arg, region_val)

        case "cursor_pos":
            region_val = get_arg("region", "")
            coords: str = _do_cursor_pos(region_val)
            sys.stdout.write(coords + "\n")
            sys.stdout.flush()

        case "select_region":
            region_result, code = _do_select_region()
            if code == EXIT_CANCEL:
                raise SystemExit(EXIT_CANCEL)
            if region_result:
                sys.stdout.write(region_result + "\n")
                sys.stdout.flush()
            raise SystemExit(EXIT_OK)

        case _:
            sys.stderr.write(f"unknown command: {command}\n")
            sys.stderr.flush()
            raise SystemExit(1)


if __name__ == "__main__":
    main()
