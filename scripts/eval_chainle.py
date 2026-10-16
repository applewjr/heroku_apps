"""Measure the Chainle solver on hand-written rounds (datasets/chainle_eval.tsv).

    python scripts/eval_chainle.py               # the shipped data file
    python scripts/eval_chainle.py --data X.npz  # another build
    python scripts/eval_chainle.py --misses      # also list the rounds it missed

Reports, per round type, how often an accepted answer is the solver's first
pick, in its top 3 and in its top 10; per-round latency (p50/p95); and the
memory loading the data costs. The rounds are written from scratch, so these
numbers say how the ranking behaves, not what a real day of the game scores.
"""

import argparse
import collections
import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, os.pardir, 'datasets')
sys.path.insert(0, os.path.join(HERE, os.pardir))

from functions import chainle  # noqa: E402

LIMIT = 100


def rss_mb():
    """This process's resident memory in MB (Linux or Windows), or None."""
    try:
        with open('/proc/self/status') as fh:
            for line in fh:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    if sys.platform == 'win32':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                    'PagefileUsage', 'PeakPagefileUsage')]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        # 64-bit: the process handle must stay a pointer, not an int.
        current = ctypes.windll.kernel32.GetCurrentProcess
        current.restype = ctypes.c_void_p
        info = ctypes.windll.psapi.GetProcessMemoryInfo
        info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if info(current(), ctypes.byref(c), c.cb):
            return c.WorkingSetSize / 2 ** 20
    return None


def load_rounds(path):
    rounds = []
    with open(path, encoding='utf-8') as fh:
        lines = [line.rstrip('\n') for line in fh if line.strip() and not line.startswith('#')]
    for line in lines[1:]:
        group, clues, answers = line.split('\t')
        rounds.append((group, clues.split(), [a.upper() for a in answers.split('|')]))
    return rounds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', default=os.path.join(DATA, 'chainle_data.npz'))
    ap.add_argument('--rounds', default=os.path.join(DATA, 'chainle_eval.tsv'))
    ap.add_argument('--misses', action='store_true', help='list rounds without an answer in the top 3')
    args = ap.parse_args()

    before = rss_mb()
    t = time.perf_counter()
    data = chainle.Data.from_file(args.data)
    load_s = time.perf_counter() - t
    after = rss_mb()

    hits = collections.defaultdict(lambda: [0, 0, 0, 0])   # group -> [rounds, top1, top3, top10]
    times, misses = [], []
    for group, clues, answers in load_rounds(args.rounds):
        kind = 'meaning' if group == 'closest' else 'bridge'
        t = time.perf_counter()
        res = chainle.solve(data, clues, kind, top=LIMIT)
        times.append(time.perf_counter() - t)
        words = [s['word'] for s in res['suggestions']]
        rank = min((words.index(a) + 1 for a in answers if a in words), default=None)
        h = hits[group]
        h[0] += 1
        for k, cut in ((1, 1), (2, 3), (3, 10)):
            h[k] += rank is not None and rank <= cut
        if rank is None or rank > 3:
            misses.append((group, ' + '.join(clues), '|'.join(answers), rank, words[:5], res['unknown']))

    print(f'{"group":10} {"rounds":>6} {"top 1":>7} {"top 3":>7} {"top 10":>7}')
    total = [0, 0, 0, 0]
    for group, h in hits.items():
        print(f'{group:10} {h[0]:>6} ' + ' '.join(f'{h[k]:>4}/{h[0]:<2}' for k in (1, 2, 3)))
        total = [a + b for a, b in zip(total, h)]
    print(f'{"all":10} {total[0]:>6} ' + ' '.join(f'{total[k]:>4}/{total[0]:<2}' for k in (1, 2, 3)))

    times.sort()
    p50, p95 = times[len(times) // 2], times[int(len(times) * 0.95)]
    print(f'\nper round: p50 {p50 * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms '
          f'(load {load_s:.2f} s, {data.size} words)')
    if before is not None and after is not None:
        print(f'memory: +{after - before:.0f} MB to load ({after:.0f} MB resident), '
              f'file {os.path.getsize(args.data) / 1e6:.1f} MB')
    if args.misses:
        print('\nnot in the top 3:')
        for group, clues, answers, rank, top5, unknown in misses:
            extra = f' unknown {unknown}' if unknown else ''
            print(f'  {group:8} {clues:28} -> {answers:22} rank {rank}  top: {", ".join(top5)}{extra}')


if __name__ == '__main__':
    main()
