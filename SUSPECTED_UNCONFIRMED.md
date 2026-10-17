# Suspected but unconfirmed items

Items I traced through the code but could not fully reproduce without a live
X11/Codex environment.

## 1. `ts_re` regex requires the year, but Codex may show "Today/Tomorrow"

**Location:** `TS_RE` (line 37), `find_reset_time` (line 170)

**Issue:** The regex `\d{4}` requires a 4-digit year in the capture group.
Codex sometimes displays "try again at Today at 3:12 AM" or "tomorrow at …"
(before the exact date is known). These would return `None` — the script
would never detect the reset time and would poll forever.

**Confirm on a real machine:** Run `auto_continue.py -v` right after hitting
the limit; observe whether the banner says "Today" or "tomorrow" or always
contains the full date. If it skips the date, add an alternate regex branch.

## 2. `abs_geometry` returns `(0,0,0,0)` on error — no retry

**Location:** `abs_geometry` (line 137), `geom_of` (line 105)

**Issue:** When Xlib fails (window destroyed between detection and geometry
read), the fallback is `(0,0,0,0)`. With the new guard in `grab_region`, this
now skips the iteration gracefully, but the window is never retried until the
next poll cycle. If this happens repeatedly, the script may miss the reset.

**Confirm:** Run `--list` while rapidly switching workspaces; see whether any
window ever returns 0 dimensions. The fix is adequate for now.

## 3. `sh()` swallows all subprocess failures

**Location:** `sh` (line 59)

**Issue:** `subprocess.run` with no `check=True` means tools like `wmctrl`,
`xdotool`, or `tesseract` can fail silently. If `tesseract` is missing,
`wmctrl` not installed, or `xdotool` returns a non‑zero exit code,
`sh()` returns `""` and the script continues doing nothing — the user
sees "watching…" forever with no error.

**Confirm:** Run without `tesseract-ocr` installed: the script should print
a clear error at startup. A check at `main()` entry (`sh("which",
"tesseract-ocr")`) would catch this.

## 4. `find_input` uses pixel arithmetic that assumes 1:1 scaling

**Location:** `find_input` (line 183)

**Issue:** The OCR bounding boxes come from the upscaled image (2×). The
coordinates are divided by `SCALE` to convert back to crop-local. If
the Codex window is on a HiDPI screen and already internally scaled,
the 2× upscale may double the error. OCR might return coordinates that
are off by a factor of 2× or more, causing clicks in the wrong place.

**Confirm:** Run `--dry -v` on a HiDPI (200%+) display and check whether
the reported input coordinates fall inside the actual input box.

## 5. `find_blue_button` uses hardcoded color thresholds

**Location:** `find_blue_button` (line 195)

**Issue:** The mask `(b > 170) & (r < 70) & (g > 80) & (g < 160) & (b-r > 120)`
assumes the Codex send button is always that specific blue. A theme update,
dark mode, or accessibility color change by the Codex team would make the
button invisible to the script. The fallback (`xdotool key Return`) still
works, so this is not a crash — but it may stop typing "continue" and just
press Enter.

**Confirm:** Run `--dry -v` after a Codex UI update and check whether the
blue-button position is still found.

## 6. `--test` mode has no `--dry` guard

**Location:** `main` (line 293–297)

**Issue:** `--test` and `--dry` can both be passed. `--dry` is checked only
inside `act()` (line 243), where it returns early before typing but after
finding coordinates. `--test` calls `act()` in a loop, so `--test --dry`
will repeatedly OCR and report coordinates every 30s — not harmful, but
misleading.

**Confirm:** `python3 auto_continue.py --test --dry` — it should print a
warning or ignore `--dry`.