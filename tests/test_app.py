"""The malloc threshold pin (helpers.pin_malloc_thresholds) and where app.py
calls it.

The pin is the fix for the dyno's memory creep: without it, glibc keeps each
thread arena's freed memory, ratcheting up to the largest block freed so far.
It only works if it runs before data.py and pandas allocate, so the order in
app.py is tested too.
"""

from pathlib import Path

import helpers

APP_PY = Path(__file__).resolve().parents[1] / "app.py"


class _FakeLibc:
    def __init__(self):
        self.calls = []

    def mallopt(self, param, value):
        self.calls.append((param, value))
        return 1


def test_pin_sets_both_thresholds_to_128_kb(monkeypatch):
    libc = _FakeLibc()
    monkeypatch.setattr(helpers.ctypes, "CDLL", lambda name: libc)
    helpers.pin_malloc_thresholds()
    # M_TRIM_THRESHOLD (-1) and M_MMAP_THRESHOLD (-3). Setting either one
    # also turns off glibc's dynamic raising, which is the point.
    assert libc.calls == [(-1, 131072), (-3, 131072)]


def test_pin_does_nothing_without_glibc(monkeypatch):
    def no_glibc(name):
        raise OSError("libc.so.6: cannot open shared object file")
    monkeypatch.setattr(helpers.ctypes, "CDLL", no_glibc)
    helpers.pin_malloc_thresholds()   # Windows and macOS: no error


def test_release_memory_does_nothing_without_glibc(monkeypatch):
    def no_glibc(name):
        raise OSError("no glibc")
    monkeypatch.setattr(helpers.ctypes, "CDLL", no_glibc)
    helpers.release_memory()


STATUS = "Name:\tgunicorn\nVmHWM:\t  420000 kB\nVmRSS:\t  378880 kB\n"


def test_rss_counts_what_herokus_quota_counts(tmp_path):
    # RssAnon, not VmRSS: VmRSS also counts shared library pages, which read
    # 50-70 MB over Heroku Metrics and held ICE COLD builds back (2026-11-21).
    status = tmp_path / "status"
    status.write_text(STATUS + "RssAnon:\t  307200 kB\nRssFile:\t   71680 kB\n")
    assert helpers.rss_mb(status) == 300


def test_rss_falls_back_to_vmrss_without_rssanon(tmp_path):
    status = tmp_path / "status"
    status.write_text(STATUS)
    assert helpers.rss_mb(status) == 370


def test_rss_is_none_without_proc(tmp_path):
    assert helpers.rss_mb(tmp_path / "missing") is None


def test_app_pins_before_anything_big_loads():
    # The routes import data.py (the word lists) and pandas. A pin after them
    # would leave whatever they freed while loading to the old thresholds.
    src = APP_PY.read_text(encoding="utf-8")
    pin = src.index("pin_malloc_thresholds()")
    for later in ("from extensions import", "from routes import"):
        assert pin < src.index(later), later
