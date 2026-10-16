#!/usr/bin/env python3
"""Find the Codex window by process/window class (not by OCR guessing), then:
  - OCR only that window to read the "try again at / resets on <timestamp>" text
  - when the time is reached: raise the window, click the input, type 'continue',
    click the blue send button, restore your previous window + mouse position.

Setup (Ubuntu, X11 session):
    sudo apt install tesseract-ocr wmctrl xdotool python3-xlib
    pip install mss pytesseract pillow numpy python-xlib
Run:
    python3 auto_continue.py --list           # show all windows: id, pid, process, class, title
    python3 auto_continue.py --dry            # find window + input + button, no clicks
    python3 auto_continue.py --test           # every 30s: type "Test", wait 2s, click send
    python3 auto_continue.py                  # real run
    python3 auto_continue.py --match "codex|chatgpt"   # override window matcher (regex)
"""
import argparse, os, re, subprocess, sys, time
from datetime import datetime

import mss
import numpy as np
import pytesseract
from PIL import Image
from Xlib import display as xdisplay

MSS = getattr(mss, "MSS", None) or mss.mss   # mss.mss is deprecated in newer versions

POLL_SECONDS = 20
EXTRA_DELAY = 5            # seconds to wait after the reset time
MESSAGE = "continue"
TEST_MESSAGE = "Test"
TEST_INTERVAL = 30
SEND_DELAY = 2             # seconds between typing and clicking send (voice -> send icon switch)
INPUT_HINT = "anything"    # word in the input placeholder ("Do anything")
SCALE = 2                  # OCR upscale

TS_RE = re.compile(
    r"(?:again\s+at|resets?\s+on)\s+([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4},?\s+\d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)

ap = argparse.ArgumentParser()
ap.add_argument("--list", action="store_true")
ap.add_argument("--dry", action="store_true")
ap.add_argument("--test", action="store_true")
ap.add_argument("--match", default="codex", help="regex matched against process name/exe/WM class")
ap.add_argument("--monitor", type=int, help="skip window detection: treat this monitor (1, 2, ...) as the Codex screen")
ap.add_argument("--region", help="skip window detection: manual screen region 'x,y,w,h'")
ap.add_argument("-v", "--verbose", action="store_true", help="human readable step-by-step logs")
args = ap.parse_args()


def vlog(msg):
    if args.verbose:
        print(f"[{datetime.now():%H:%M:%S}]   {msg}")


# ---------- windows / processes ----------
def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def proc_info(pid):
    """(comm, exe basename, argv0 basename) for a pid, '' when unreadable."""
    def rd(p):
        try:
            return open(p, "rb").read().decode(errors="ignore")
        except OSError:
            return ""
    comm = rd(f"/proc/{pid}/comm").strip()
    try:
        exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
    except OSError:
        exe = ""
    argv0 = os.path.basename(rd(f"/proc/{pid}/cmdline").split("\0")[0])
    return comm, exe, argv0


def list_windows():
    """Parse `wmctrl -lpGx`: id desktop pid x y w h class host title"""
    wins = []
    for line in sh("wmctrl", "-lpGx").splitlines():
        p = line.split(None, 9)
        if len(p) < 9:
            continue
        pid = int(p[2])
        comm, exe, argv0 = proc_info(pid) if pid > 0 else ("", "", "")
        wins.append(dict(id=int(p[0], 16), desktop=p[1], pid=pid, cls=p[7],
                         title=p[9] if len(p) > 9 else "", comm=comm, exe=exe, argv0=argv0))
    return wins


def monitor_geometry(n):
    with MSS() as sct:
        if not 1 <= n < len(sct.monitors):
            sys.exit(f"--monitor {n} invalid; available: 1..{len(sct.monitors) - 1} (see --list)")
        m = sct.monitors[n]
        return m["left"], m["top"], m["width"], m["height"]


def wid_str(w):
    return hex(w["id"]) if w["id"] is not None else "manual"


def geom_of(w):
    return w["geom"] if w["id"] is None else abs_geometry(w["id"])


def find_codex():
    # manual override: no window detection, you tell us where Codex is
    if args.region or args.monitor:
        if args.region:
            geom = tuple(int(v) for v in args.region.split(","))
            vlog(f"manual region {geom}")
        else:
            geom = monitor_geometry(args.monitor)
            vlog(f"manual monitor {args.monitor}: {geom}")
        return dict(id=None, pid=0, comm="manual", cls="manual", title="manual screen", geom=geom)
    rx = re.compile(args.match, re.I)
    # 1) process name / exe / WM class  (reliable: a browser tab titled "Codex" won't match)
    wins = list_windows()
    hits = [w for w in wins
            if rx.search(w["comm"]) or rx.search(w["exe"]) or rx.search(w["argv0"]) or rx.search(w["cls"])]
    vlog(f"scanned {len(wins)} open windows, {len(hits)} match /{args.match}/ (process name or window class)")
    if not hits:
        return None
    for h in hits:
        vlog(f"candidate: window {hex(h['id'])} pid={h['pid']} process={h['comm']} class={h['cls']} title={h['title'][:50]!r}")
    # Electron may expose several windows; prefer the biggest
    hits.sort(key=lambda w: -(abs_geometry(w["id"])[2] * abs_geometry(w["id"])[3]))
    return hits[0]


_dpy = xdisplay.Display()


def abs_geometry(wid):
    """(x, y, w, h) of the window's client area in root (screen) coordinates."""
    try:
        win = _dpy.create_resource_object("window", wid)
        g = win.get_geometry()
        r = _dpy.screen().root.translate_coords(win, 0, 0)
        return r.x, r.y, g.width, g.height
    except Exception:
        return 0, 0, 0, 0


def activate(wid):
    vlog(f"bringing window {hex(wid)} to the front (un-minimize / switch workspace if needed)")
    sh("wmctrl", "-i", "-a", hex(wid))   # raises, un-minimizes, switches workspace
    time.sleep(1.0)


# ---------- screen / OCR (window region only) ----------
def grab_region(x, y, w, h):
    with MSS() as sct:
        vs = sct.monitors[0]  # virtual screen bounds
        x0, y0 = max(x, vs["left"]), max(y, vs["top"])
        x1 = min(x + w, vs["left"] + vs["width"])
        y1 = min(y + h, vs["top"] + vs["height"])
        shot = sct.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), x0, y0


def ocr(img):
    big = img.resize((img.width * SCALE, img.height * SCALE))
    return pytesseract.image_to_data(big, output_type=pytesseract.Output.DICT)


def find_reset_time(data):
    text = " ".join(w for w in data["text"] if w.strip())
    m = TS_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"\s+", " ", m.group(1).replace(",", " ")).strip()
    raw = re.sub(r"(\d)([AP]M)", r"\1 \2", raw, flags=re.I).upper()
    try:
        return datetime.strptime(raw, "%b %d %Y %I:%M %p")
    except ValueError:
        return None


def find_input(data):
    """(x, y) in crop-local pixels of the lowest placeholder match."""
    best = None
    for i, w in enumerate(data["text"]):
        if INPUT_HINT in w.lower():
            x = (data["left"][i] + data["width"][i] // 2) // SCALE
            y = (data["top"][i] + data["height"][i] // 2) // SCALE
            if best is None or y > best[1]:
                best = (x, y)
    return best


def find_blue_button(img, ix, iy):
    """Centroid (crop-local) of bright-blue pixels right of / around the input line."""
    a = np.asarray(img).astype(int)
    y0, y1 = max(iy - 60, 0), min(iy + 90, a.shape[0])
    reg = a[y0:y1, ix:]
    r, g, b = reg[..., 0], reg[..., 1], reg[..., 2]
    mask = (b > 170) & (r < 70) & (g > 80) & (g < 160) & (b - r > 120)
    ys, xs = np.nonzero(mask)
    if len(xs) < 30:
        return None
    return int(xs.mean()) + ix, int(ys.mean()) + y0


# ---------- input ----------
def click(x, y):
    sh("xdotool", "mousemove", str(x), str(y), "click", "1")


def type_text(text):
    sh("xdotool", "type", "--clearmodifiers", "--delay", "60", "--", text)


# ---------- action ----------
def act(message=MESSAGE):
    w = find_codex()
    if not w:
        print("Codex window not found")
        return False
    prev_win = sh("xdotool", "getactivewindow")
    prev_mouse = sh("xdotool", "getmouselocation", "--shell")
    mx = re.search(r"X=(\d+)", prev_mouse)
    my = re.search(r"Y=(\d+)", prev_mouse)

    if w["id"] is not None:
        activate(w["id"])
    else:
        vlog("manual screen mode: not raising any window, Codex must be visible on that screen")
    x, y, ww, hh = geom_of(w)
    vlog(f"window is {ww}x{hh} at screen position ({x}, {y}); taking a screenshot of just that area")
    img, ox, oy = grab_region(x, y, ww, hh)
    data = ocr(img)
    vlog(f"OCR read {len([t for t in data['text'] if t.strip()])} words from the window")
    pos = find_input(data)
    if not pos:
        print(f"input ('Do anything') not found inside window {wid_str(w)} ({ww}x{hh} at {x},{y})")
        return False
    ax, ay = pos[0] + ox, pos[1] + oy
    print(f"input at screen ({ax}, {ay})")
    if args.dry:
        print("blue button (before typing):", find_blue_button(img, *pos))
        return True

    vlog("clicking the input box (twice: the first click may only focus the window)")
    click(ax, ay)
    time.sleep(0.6)
    click(ax, ay)
    time.sleep(0.4)
    vlog(f"typing {message!r}")
    type_text(message)
    vlog(f"waiting {SEND_DELAY}s so the voice button switches to the send button")
    time.sleep(SEND_DELAY)

    img2, _, _ = grab_region(x, y, ww, hh)
    typed_ok = any(message.lower() in t.lower() for t in ocr(img2)["text"])
    print("typed text visible:", typed_ok)
    vlog("looking for the blue send button near the input box")
    btn = find_blue_button(img2, *pos)
    if btn:
        bx, by = btn[0] + ox, btn[1] + oy
        print(f"clicking send at screen ({bx}, {by})")
        click(bx, by)
    else:
        print("blue button not found, pressing Enter")
        sh("xdotool", "key", "Return")

    # restore previous window + mouse
    vlog("done; restoring your previous window and mouse position")
    time.sleep(0.5)
    if prev_win and prev_win != str(w["id"]):
        sh("wmctrl", "-i", "-a", hex(int(prev_win)))
    if mx and my:
        sh("xdotool", "mousemove", mx.group(1), my.group(1))
    return True


def main():
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("warning: Wayland session detected; screenshots/clicks need an X11 session")

    if args.list:
        for w in list_windows():
            print(f"{wid_str(w)}  pid={w['pid']:<7} proc={w['comm'] or '-':<16} "
                  f"class={w['cls']:<35} title={w['title'][:60]}")
        with MSS() as sct:
            for i, m in enumerate(sct.monitors[1:], start=1):
                print(f"monitor {i}: {m['width']}x{m['height']} at ({m['left']}, {m['top']})")
        return

    if args.test:
        print(f"TEST mode: sending '{TEST_MESSAGE}' every {TEST_INTERVAL}s (Ctrl+C to stop)")
        while True:
            act(TEST_MESSAGE)
            time.sleep(TEST_INTERVAL)

    print("watching... (Ctrl+C to stop)")
    target, last = None, "init"
    while True:
        w = find_codex()
        state = (w["id"], w["pid"]) if w else None
        if state != last:
            ts = datetime.now().strftime("%H:%M:%S")
            if w:
                print(f"[{ts}] GPT (Codex) detected: window {wid_str(w)} pid={w['pid']} "
                      f"proc={w['comm']} class={w['cls']} geom={geom_of(w)}")
            else:
                print(f"[{ts}] GPT (Codex) NOT running (no window matching /{args.match}/)")
            last = state
        if w:
            x, y, ww, hh = geom_of(w)
            img, _, _ = grab_region(x, y, ww, hh)
            vlog(f"checking Codex window ({ww}x{hh} at {x},{y}) for a 'try again at / resets on' message")
            t = find_reset_time(ocr(img))
            if t and t != target:
                target = t
                print("reset time:", target)
            elif not t:
                vlog("no reset message visible (window may be hidden/covered, or no limit hit)")
        if target:
            wait = (target - datetime.now()).total_seconds() + EXTRA_DELAY
            vlog(f"reset at {target:%Y-%m-%d %H:%M}, {max(wait, 0):.0f}s to go (incl. {EXTRA_DELAY}s buffer)")
            if wait <= 0:
                print("reached, acting")
                act()
                target = None
                time.sleep(60)
                continue
            time.sleep(min(wait, POLL_SECONDS))
        else:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
