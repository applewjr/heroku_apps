"""Wrapper that turns a silently-failing scheduled job into a loud one.

Every live scheduled task sends its success email as the last unguarded
statement in the file, and none of them has a main() or an
``if __name__ == "__main__"`` guard. An exception anywhere earlier kills the
process before that line, so the only evidence a job failed is an email that
never arrives - which a human has to notice. That is the gap this closes.

Rather than restructure eight working scripts, run them through here:

    python -m monitoring.run_job scheduled_tasks_youtube/youtube_trending_v2.py

runpy executes the script byte-for-byte the way ``python <path>`` does, so
every existing behaviour - the success email included - is unchanged. What is
added is a JJ_ALERT plus a non-zero exit on failure, and a JJ_PULSE on
success.
"""

import os
import runpy
import sys
import time
import traceback

from monitoring import alerts

USAGE = "usage: python -m monitoring.run_job <script.py> [args...]"


def job_name(script_path):
    """Short, stable label for the alert: 'youtube_trending_v2'."""
    return os.path.splitext(os.path.basename(script_path))[0]


def _exit_code(exc):
    """Normalise SystemExit.code, which may be None, an int, or a message."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def run(script_path, args=()):
    """Execute one script, reporting the outcome. Returns a process exit code."""
    name = job_name(script_path)

    if not os.path.isfile(script_path):
        alerts.alert('job:' + name, sev='crit', exc='FileNotFound', msg=script_path)
        print(USAGE, file=sys.stderr)
        return 2

    # Match what `python <path>` sets up, so a script that imports a sibling
    # module behaves identically under the wrapper. Running under -m already
    # puts the repo root on sys.path, so this is strictly additive.
    script_dir = os.path.dirname(os.path.abspath(script_path))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    sys.argv = [script_path] + list(args)

    started = time.monotonic()
    try:
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as e:
        # A script that calls sys.exit(0) succeeded; only a non-zero code is
        # a failure worth waking someone for.
        code = _exit_code(e)
        if code:
            return _report_failure(name, e, started, code)
    except BaseException as e:
        # BaseException rather than Exception: a job that dies on
        # KeyboardInterrupt or an unexpected BaseException subclass has still
        # failed, and silence is the failure mode being fixed here.
        return _report_failure(name, e, started, 1)

    alerts.pulse('job:' + name, ms=_elapsed_ms(started))
    return 0


def _elapsed_ms(started):
    return int((time.monotonic() - started) * 1000)


def _report_failure(name, exc, started, code):
    # Traceback first, so Papertrail shows the detail next to the alert line.
    traceback.print_exc()
    alerts.alert(
        'job:' + name, sev='crit',
        exc=type(exc).__name__, msg=exc, ms=_elapsed_ms(started),
    )
    return code


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    return run(argv[0], argv[1:])


if __name__ == "__main__":
    sys.exit(main())
