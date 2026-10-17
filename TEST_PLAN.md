# Manual test plan (real machine, Ubuntu 24.04 X11)

## Prerequisites

```bash
sudo apt install tesseract-ocr wmctrl xdotool python3-xlib python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 1. Sanity — no tool crashes

```bash
python3 auto_continue.py -h        # help screen
python3 auto_continue.py --list    # list windows + monitors. Verify:
                                   # - all open windows appear
                                   # - monitors show correct dimensions
                                   # - no traceback
```

## 2. Single monitor, `--dry` mode

```bash
python3 auto_continue.py --match codex --dry -v
```

- Expected: prints the Codex window geometry, input position, and
  blue-button position (before typing). No mouse movement, no typing.
- Verify the printed coordinates fall inside the actual input box on screen.
- If `--match codex` finds nothing, try `--match ChatGPT` or the browser's
  process name.

## 3. `--test` mode (types "Test")

```bash
python3 auto_continue.py --test -v
```

- Expected: every 30s the script raises the Codex window, clicks the input,
  types `Test`, waits 2s, then clicks Send (or presses Enter). The log
  prints `typed text visible: True` and `clicking send at screen (…)`.
- Press Ctrl+C to stop after 2–3 cycles.
- **Bug check:** verify that `--dry` is NOT also set — the script should
  actually type.

## 4. Repeat-after-act guard test

1. Run `python3 auto_continue.py -v` with Codex open and NOT at limit.
2. Wait for one poll cycle. The log should show `watching…` and periodic
   `no reset message visible` messages.
3. Open a second terminal and inspect line 308 to confirm `acted_on` is
   initialized to `None`.
4. **Bug check:** If you manually force an OCR match (e.g. by taking a
   screenshot with the banned message), the script should fire `act()` once
   and then NOT re-fire on the same timestamp. Without the fix, it would
   re-fire every ~65 seconds.

## 5. Dual-monitor

1. Drag the Codex window to monitor 2.
2. Run `python3 auto_continue.py --list` and note monitor 2's number.
3. Run `python3 auto_continue.py --monitor 2 --dry -v`.
   - Expected: the script skips window detection, reports manual mode,
     and uses monitor 2's geometry.
4. Run `python3 auto_continue.py --monitor 2 --test -v`.
   - Expected: typing and sending work on monitor 2.

## 6. Manual region override

```bash
python3 auto_continue.py --region 100,200,800,600 --dry -v
```

- Expected: uses the given rectangle as the Codex window area, no window
  detection.

## 7. Edge: Wayland session

```bash
xdg-session-type
```

If this prints `wayland`, run:
```bash
python3 auto_continue.py -v
```

- Expected: prints a warning `Wayland session detected; screenshots/clicks
  need an X11 session` then continues (and likely fails at the screenshot
  step — that's expected).

## 8. Edge: missing tesseract

```bash
sudo apt remove tesseract-ocr
python3 auto_continue.py --dry -v
```

- Expected: `pytesseract.pytesseract.TesseractNotFoundError` traceback.
  The script does NOT handle this gracefully (known limitation in the
  suspected‑unconfirmed list — item 3).
- Reinstall: `sudo apt install tesseract-ocr`

## 9. Edge: Codex not running

```bash
python3 auto_continue.py -v
```

- Expected: `[HH:MM:SS] GPT (Codex) NOT running (no window matching /codex/)`
  repeated every 20s. No crash.

## 10. Full real run (the thing it's built for)

1. Have Codex hit the usage limit and show the "try again at …" banner.
2. Run `python3 auto_continue.py -v`.
3. Verify:
   - The reset time is OCR'd and printed (e.g. `reset time: 2026-10-17 03:12:00`)
   - The countdown decreases (`XXs to go`)
   - At the reset moment the script raises the window, types `continue`,
     clicks Send, and restores your previous window and mouse position.
   - **After acting**, the script does NOT re-detect the same reset time
     and fire again (the `acted_on` guard prevents this).

---

## Regression checklist after code changes

| Test | What to check |
|------|---------------|
| 2 (`--dry`) | Coordinates match actual UI |
| 3 (`--test`) | Types and sends correctly |
| 4 (repeat guard) | No duplicate sends |
| 5 (dual-monitor) | Works on second screen |
| 7 (Wayland) | Graceful warning, no crash |
| 8 (missing tool) | Clear error, no hang |
| 9 (no Codex) | Clean message, no crash |