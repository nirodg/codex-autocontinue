"""Tests for auto_continue.py — pure logic functions, no X11 deps."""
import re
from datetime import datetime

TS_RE = re.compile(
    r"(?:again\s+at|resets?\s+on)\s+"
    r"([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4},?\s+\d{1,2}:\d{2}\s*[AP]M)",
    re.I,
)
SCALE = 2
INPUT_HINT = "anything"


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
    best = None
    for i, w in enumerate(data["text"]):
        if INPUT_HINT in w.lower():
            x = (data["left"][i] + data["width"][i] // 2) // SCALE
            y = (data["top"][i] + data["height"][i] // 2) // SCALE
            if best is None or y > best[1]:
                best = (x, y)
    return best


def mkdata(texts, lefts, tops, widths, heights):
    return dict(text=list(texts), left=list(lefts),
                top=list(tops), width=list(widths), height=list(heights))


# ======================================================================
# find_reset_time tests
# ======================================================================

def test_standard():
    data = mkdata(
        ["try", "again", "at", "Oct", "17,", "2026,", "3:12", "AM"],
        [0]*8, [0]*8, [10]*8, [10]*8)
    assert find_reset_time(data) == datetime(2026, 10, 17, 3, 12)


def test_resets_on():
    data = mkdata(
        ["resets", "on", "Dec", "25,", "2026,", "11:59", "PM"],
        [0]*7, [0]*7, [10]*7, [10]*7)
    assert find_reset_time(data) == datetime(2026, 12, 25, 23, 59)


def test_pm():
    data = mkdata(
        ["again", "at", "Nov", "5,", "2026,", "2:30", "PM"],
        [0]*7, [0]*7, [10]*7, [10]*7)
    dt = find_reset_time(data)
    assert dt.hour == 14 and dt.minute == 30


def test_no_match():
    data = mkdata(["hello", "world"], [0]*2, [0]*2, [10]*2, [10]*2)
    assert find_reset_time(data) is None


def test_no_year():
    """'again at Oct 17, 3:12 AM' — regex requires year."""
    data = mkdata(
        ["again", "at", "Oct", "17,", "3:12", "AM"],
        [0]*6, [0]*6, [10]*6, [10]*6)
    assert find_reset_time(data) is None


def test_ocr_misread():
    """OCR misreads '3:l2' instead of '3:12' — should fail gracefully."""
    data = mkdata(
        ["again", "at", "Oct", "17,", "2026,", "3:l2", "AM"],
        [0]*7, [0]*7, [10]*7, [10]*7)
    assert find_reset_time(data) is None


def test_noise_around_timestamp():
    data = mkdata(
        ["limit", "try", ".", "again", "at", "Oct", "17,",
         "2026,", "3:12", "AM", "more", "stuff"],
        [0]*12, [0]*12, [10]*12, [10]*12)
    assert find_reset_time(data) == datetime(2026, 10, 17, 3, 12)


# ======================================================================
# find_input tests
# ======================================================================

def test_finds_lowest_anything():
    data = mkdata(
        ["Do", "anything", "or", "type", "/"],
        [10, 30, 80, 110, 140],
        [100, 100, 100, 100, 100],
        [20, 40, 20, 30, 10],
        [10, 10, 10, 10, 10])
    pos = find_input(data)
    # "anything": left=30, w=40 => center=(30+20)//2//2 = 12
    # top=100, h=10 => center=(100+5)//2 = 52
    assert pos == (12, 52)


def test_prefers_lower_y():
    """Two 'anything' matches: pick the one with bigger y."""
    data = mkdata(
        ["anything", "anything"],
        [10, 50], [100, 150], [20, 20], [10, 10])
    pos = find_input(data)
    # second: left=50 w=20 => (50+10)//4 = 15, top=150 h=10 => (150+5)//2 = 77
    assert pos == (15, 77)


def test_input_no_match():
    data = mkdata(["hello", "world"], [0]*2, [0]*2, [10]*2, [10]*2)
    assert find_input(data) is None


# ======================================================================
# repeat-after-act guard test (main() logic simulation)
# ======================================================================

def test_repeat_after_act_guard():
    """After act() fires with target T, the next OCR pass should NOT
    re-set target to T again."""
    target = None
    acted_on = None

    data = mkdata(
        ["again", "at", "Oct", "17,", "2026,", "3:12", "AM"],
        [0]*7, [0]*7, [10]*7, [10]*7)

    # First detection
    t = find_reset_time(data)
    if t and t != target and t != acted_on:
        target = t

    assert target == datetime(2026, 10, 17, 3, 12)

    # Simulate act() finishing
    acted_on = target
    target = None

    # Next OCR pass: same timestamp still visible
    t2 = find_reset_time(data)
    will_reset = t2 and t2 != target and t2 != acted_on
    assert will_reset is False, "Should not re-detect already-acted-on time"