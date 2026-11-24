"""The 4x6 solver: the fewest moves for a board, exactly, by search.

4x6 is a color-sorting puzzle with nothing hidden and no luck. Six shelves
hold four tiles each, four of each of six colors, and beside them is the
Stage, which starts empty. A move takes the run of one color at a shelf's
right end and puts it on an empty shelf, or next to the same color, as much
of it as fits; a player can't move part of a run that would fit. The Stage
works as a seventh shelf. The board is done when every shelf is one color
and the Stage is empty: that ends the game.

A position is eight strings of color codes: the six shelves and the Stage,
each read left to right, then the Extra pocket ('' when it's free, a code
while it holds a tile, SPENT once it's been used). A starting board has full
shelves and nothing else; a player can also bring one from partway through a
game. The shelves' order doesn't change how many moves are left, so
positions that only swap shelves count as one.

The search is A*: it always tries next the position with the lowest (moves
made so far + at least this many left). "At least this many left" is (runs
of one color on the board - 6) + (colors not yet at the left end of any
shelf) + (1 for a tile in Extra). A finished board has six runs, every
color at a left end and nothing in Extra, and one move changes the total by
at most one, so the estimate is never more than the truth, and the first
finished position the search takes is the shortest way. A board the search
runs out of positions on can't be finished without a tool. Real boards take
a few hundred positions, about 2 ms.

The fewest moves never use a tool (Scoop, Flip, Extra): every daily board is
solvable without one, using one costs points, and the game's Restart is free
(it clears moves, undos and tools), so a stuck player is better off
restarting. The rainbow route, for a badge that allows tools and is played
on a replay, may use Extra: without it most boards can't finish in rainbow
order, and with it every board tried could.
"""

import heapq
import itertools
from collections import namedtuple
from functools import lru_cache

SHELVES = 6
WIDTH = 4
TILES = SHELVES * WIDTH
STAGE = SHELVES
EXTRA = SHELVES + 1
# Extra after its tile has gone back on the board: used, and empty.
SPENT = '-'

# Our own codes, in the order of the rainbow finish, top shelf to bottom.
COLORS = 'YOKPBG'
NAMES = {'Y': 'yellow', 'O': 'orange', 'K': 'pink', 'P': 'purple', 'B': 'blue', 'G': 'green'}

# The game shows Par as the fewest moves plus this.
PAR_OVER_FEWEST = 2
# A search stops after this many positions: a cap on what one request can
# cost. Measured with no cap, random boards and hill-climbs for the worst
# (2026-11-23): the fewest moves closed 1,302 at most over 528,000 boards,
# starting and partway, and a rainbow route 6,136 at most over 392,000
# searches of solvable starting boards. So nothing is expected to reach it,
# and a search that does says so (SearchLimit).
POSITION_CAP = 20000
# Moves a request may carry: a whole game is about 20.
MAX_MOVES = 60

Step = namedtuple('Step', 'src dst n color after')


class BoardError(ValueError):
    """A board, or a move on it, that can't be."""


class SearchLimit(Exception):
    """A search stopped at POSITION_CAP, so whether there's a way, and how
    short, isn't known."""


##### boards #####

def _codes(text, what):
    if not set(text) <= set(COLORS):
        raise BoardError(f'every tile {what} must be one of the six colors')
    return text


def parse_board(text):
    """A position from the page: a starting board as its 24 codes, shelf by
    shelf, left to right; or a board partway through a game as eight
    '|'-joined parts (the six shelves, the Stage and Extra, each left to
    right, with Extra as SPENT once it's been used). BoardError if it isn't
    one."""
    if not isinstance(text, str):
        raise BoardError('the board must be 24 tiles')
    text = text.strip().upper()
    if '|' not in text:
        if len(text) != TILES:
            raise BoardError('the board must be 24 tiles')
        parts = [text[i:i + WIDTH] for i in range(0, TILES, WIDTH)] + ['', '']
    else:
        parts = text.split('|')
        if len(parts) != EXTRA + 1:
            raise BoardError('a board is six shelves, the Stage and Extra')
    for i, part in enumerate(parts):
        if i == EXTRA and part == SPENT:
            continue
        _codes(part, 'on the board')
        if len(part) > (1 if i == EXTRA else WIDTH):
            raise BoardError('Extra holds one tile' if i == EXTRA else 'a shelf holds four tiles')
    if len(set(parts[STAGE])) > 1:
        raise BoardError('the Stage holds one color at a time')
    tiles = ''.join(parts)
    off = [c for c in COLORS if tiles.count(c) != WIDTH]
    if off:
        raise BoardError('each color must be there four times: check ' + ', '.join(NAMES[c] for c in off))
    return tuple(parts)


def is_start(position):
    """A starting board: full shelves, nothing on the Stage, Extra unused."""
    return all(len(s) == WIDTH for s in position[:SHELVES]) and not position[STAGE] and not position[EXTRA]


def board_text(position):
    """A position as parse_board takes it: 24 codes for a starting board,
    else its eight parts."""
    if is_start(position):
        return ''.join(position[:SHELVES])
    return '|'.join(position)


def holding(position):
    """The tile in Extra, or ''."""
    pocket = position[EXTRA]
    return '' if pocket == SPENT else pocket


##### moves #####

def run_length(shelf):
    """How many tiles of one color are at a shelf's right end."""
    n = 0
    while n < len(shelf) and shelf[-1 - n] == shelf[-1]:
        n += 1
    return n


def moved(position, src, dst):
    """How many tiles a move takes, or 0 if the game wouldn't allow it.
    Extra gives or takes one tile: into it only while it's free (the tool),
    out of it onto an empty shelf or next to the same color."""
    if src == dst:
        return 0
    if dst == EXTRA:
        return 1 if position[src] and position[EXTRA] == '' else 0
    source, target = holding(position) if src == EXTRA else position[src], position[dst]
    if not source or len(target) >= WIDTH or (target and target[-1] != source[-1]):
        return 0
    return 1 if src == EXTRA else min(run_length(source), WIDTH - len(target))


def _after(position, src, dst, n):
    shelves = list(position)
    color = holding(position) if src == EXTRA else shelves[src][-1]
    shelves[src] = SPENT if src == EXTRA else shelves[src][:-n]
    shelves[dst] = color if dst == EXTRA else shelves[dst] + color * n
    return tuple(shelves)


def apply_move(position, src, dst):
    """The position after a move. BoardError if it isn't legal, or if the
    board is finished already: that ended the game."""
    if not (type(src) is int and type(dst) is int and 0 <= src <= EXTRA and 0 <= dst <= EXTRA):
        raise BoardError('a move goes from one place on the board to another')
    if is_done(position):
        raise BoardError('the board is finished')
    n = moved(position, src, dst)
    if not n:
        raise BoardError("that move isn't allowed")
    return _after(position, src, dst, n)


def legal_moves(position, tool=False):
    """[(src, dst, n)] for every move the game allows: a tile in Extra back
    out, but into Extra only with `tool`."""
    out = []
    for src in range(EXTRA + 1):
        for dst in range(EXTRA + 1 if tool else EXTRA):
            n = moved(position, src, dst)
            if n:
                out.append((src, dst, n))
    return out


def is_done(position):
    """Every shelf one color, the Stage empty, nothing held in Extra."""
    return (not position[STAGE] and not holding(position)
            and all(s == s[:1] * WIDTH for s in position[:SHELVES]))


def auto_finish(position):
    """The move 4x6 makes by itself, as (src, dst, n), or None. When the
    Stage's tiles, moved onto a shelf, would finish the board, the game moves
    them there a moment later (unless the player touches the board first),
    on the first shelf that works; a tile held in Extra stops it (its page
    code, 2026-11-23). So a route can't pass through such a position unless
    that move is the one it wants."""
    stage = position[STAGE]
    if not stage or holding(position):
        return None
    n = len(stage)
    for i in range(SHELVES):
        shelf = position[i]
        if (shelf and shelf[-1] != stage[-1]) or WIDTH - len(shelf) < n:
            continue
        if is_done(_after(position, STAGE, i, n)):
            return STAGE, i, n
    return None


##### the search #####

def _runs(shelf):
    return sum(1 for i, c in enumerate(shelf) if i == 0 or shelf[i - 1] != c)


def at_least(position):
    """The fewest moves a position could possibly need (see the module
    docstring): never more than it does need."""
    runs = sum(map(_runs, position[:EXTRA]))
    homes = {s[0] for s in position[:SHELVES] if s}
    return runs - SHELVES + len(COLORS) - len(homes) + bool(holding(position))


def _at_least_in_order(position, order):
    """at_least, to finish with the colors on the shelves in `order`."""
    runs = sum(map(_runs, position[:EXTRA]))
    return (runs - SHELVES + sum(1 for s, c in zip(position, order) if s[:1] != c)
            + bool(holding(position)))


def _same_shelves(position):
    return tuple(sorted(position[:SHELVES])), position[STAGE], position[EXTRA]


def _search(start, key, estimate, done, tool=False):
    """The shortest way from `start` to a position that is `done`, as
    [(src, dst, n)], or None if there is none. SearchLimit if it reaches
    POSITION_CAP first. Positions with the same `key` count as one. As in the game, a finished
    board ends the game, and a position the game finishes by itself
    (auto_finish) has only that move. `tool` lets a tile into Extra."""
    best = {key(start): 0}
    came_from = {}
    order = itertools.count(1)
    queue = [(estimate(start), 0, 0, start)]
    closed = set()
    while queue:
        _total, _tie, made, position = heapq.heappop(queue)
        k = key(position)
        if k in closed:
            continue
        closed.add(k)
        if done(position):
            path = []
            while position in came_from:
                move, position = came_from[position]
                path.append(move)
            return path[::-1]
        if len(closed) >= POSITION_CAP:
            raise SearchLimit
        if is_done(position):
            continue
        forced = auto_finish(position)
        for src, dst, n in [forced] if forced else legal_moves(position, tool):
            after = _after(position, src, dst, n)
            after_key = key(after)
            if made + 1 < best.get(after_key, made + 2):
                best[after_key] = made + 1
                came_from[after] = ((src, dst, n), position)
                # Among equals, the newest first: straight on toward a finish.
                heapq.heappush(queue, (made + 1 + estimate(after), -next(order), made + 1, after))
    return None


def _steps(position, path):
    """A path as Steps, each with the position after it."""
    out = []
    for src, dst, n in path:
        color = holding(position) if src == EXTRA else position[src][-1]
        position = _after(position, src, dst, n)
        out.append(Step(src, dst, n, color, position))
    return tuple(out)


# A search's outcome when it reached the cap, kept like any other.
_CAPPED = object()


@lru_cache(maxsize=512)
def _outcome(position):
    try:
        path = _search(position, _same_shelves, at_least, is_done)
    except SearchLimit:
        return _CAPPED
    return None if path is None else _steps(position, path)


def solve(position):
    """The fewest moves from a position to a finished board, with no tool,
    as Steps (none if it's finished already), or None if it can't be
    finished. SearchLimit if the search couldn't tell."""
    steps = _outcome(position)
    if steps is _CAPPED:
        raise SearchLimit
    return steps


def _rainbow_route(position, tool):
    """(the shorter of the two orders' paths or None, whether a search
    reached the cap)."""
    best, capped = None, False
    for order in (COLORS, COLORS[::-1]):
        try:
            path = _search(position, lambda p: p, lambda p, o=order: _at_least_in_order(p, o),
                           lambda p, o=order: is_done(p) and all(s[:1] == c for s, c in zip(p, o)), tool)
        except SearchLimit:
            capped = True
            continue
        if path is not None and (best is None or len(path) < len(best)):
            best = path
    return best, capped


@lru_cache(maxsize=256)
def rainbow(position):
    """(steps, proved): the fewest moves to a board finished in rainbow order
    (COLORS top to bottom, or bottom to top), as Steps, with no tool if
    that's as short, else using Extra once; None if none was found. proved
    is False if a search reached the cap, so there may be a shorter way, or
    one at all."""
    path, capped = _rainbow_route(position, False)
    if position[EXTRA] == '':
        with_extra, extra_capped = _rainbow_route(position, True)
        # Anything without the tool is a way with it too, so this search
        # alone settles the fewest.
        capped = extra_capped
        if path is None or (with_extra is not None and len(with_extra) < len(path)):
            path = with_extra
    return (None if path is None else _steps(position, path)), not capped


##### points #####

# What a daily scores, the game's own lines (its page code, 2026-11-23). The
# first is 100 on a Monday and 5 more each day after it.
SOLVED, SOLVED_PER_DAY = 100, 5
NO_TOOLS = (100, 50, 25, 0)          # by tools used
NO_UNDOS = 50
UNDER_PAR, AT_PAR = 25, 10
FEWEST = 50
STREAK_MAX = 50


def points(weekday, moves, fewest, undos=0, tools=0, streak=0):
    """A daily solve's score. weekday: Monday 0 to Sunday 6."""
    par = fewest + PAR_OVER_FEWEST
    score = SOLVED + SOLVED_PER_DAY * weekday + NO_TOOLS[tools]
    if undos == 0:
        score += NO_UNDOS
    if moves < par:
        score += UNDER_PAR
    elif moves == par:
        score += AT_PAR
    if moves <= fewest:
        score += FEWEST
    return score + min(STREAK_MAX, streak)


def short_of_best(total, fewest):
    """The points a solve in `total` moves gives up for its moves alone."""
    return points(0, fewest, fewest) - points(0, total, fewest)


##### what the page gets #####

def _json_steps(steps):
    return [{'from': s.src, 'to': s.dst, 'n': s.n, 'color': s.color, 'after': list(s.after)} for s in steps]


def _homes(position):
    """{color: its shelf} on a finished board."""
    return {s[0]: i for i, s in enumerate(position[:SHELVES]) if s}


def _stage_colors(position, steps):
    """The colors the Stage holds, in order: the one on it already, then
    each as it first goes on."""
    out = list(position[STAGE][:1])
    for step in steps:
        if step.dst == STAGE and step.color not in out:
            out.append(step.color)
    return out


def plan(position, moves=(), with_rainbow=False):
    """Everything the page shows for a board (a starting one, or one from
    partway through a game) and the moves a player has made on it since
    ([(src, dst)]). BoardError for a move that isn't legal.

    status is 'start' (no moves yet), 'on_track' (the fewest moves in all is
    still possible), 'off' (more than that now), 'dead_end' (it can't be
    finished without a tool) or 'done'. 'unsolvable' is a board with no
    solution, before any move; after moves on one (a tile into Extra may
    open a way), 'continued' is a way on, with no fewest or par to measure
    it by. 'unknown' is a search that reached its cap: nothing is claimed.
    solvable is True, False, or None when that isn't known.

    `start_board` says whether it's a starting board, whose par the game
    shows. The rainbow route (rainbow: None if none was found;
    rainbow_proved: whether that's settled) is worked out only when asked
    for, as it takes longer, and only for a starting board with no moves
    made.
    """
    start, known = _try_solve(position)
    now = position
    for src, dst in moves:
        now = apply_move(now, src, dst)
    made = len(moves)
    first = is_start(position)
    result = {'solvable': start is not None if known else None, 'made': made, 'position': list(now),
              'start_board': first}
    if start is None and not made:
        result['status'] = 'unsolvable' if known else 'unknown'
        return result
    steps, known = _try_solve(now) if made else (start, True)
    if start is not None:
        fewest = len(start)
        result['fewest'] = fewest
        result['par'] = fewest + PAR_OVER_FEWEST
    if steps is None:
        result.update(status='dead_end' if known else 'unknown', steps=[])
    else:
        total = made + len(steps)
        if not steps:
            status = 'done'
        elif not made:
            status = 'start'
        elif start is None:
            status = 'continued'
        else:
            status = 'on_track' if total == fewest else 'off'
        result.update(status=status, steps=_json_steps(steps), left=len(steps), total=total,
                      homes=_homes(steps[-1].after if steps else now), stage_colors=_stage_colors(now, steps))
        if start is not None:
            result.update(gold=total <= fewest + PAR_OVER_FEWEST, short=short_of_best(total, fewest))
    if with_rainbow and first and not made:
        route, proved = rainbow(position)
        result.update(rainbow=None, rainbow_proved=proved)
        if route is not None:
            tool = any(s.dst == EXTRA for s in route)
            result['rainbow'] = {'moves': len(route), 'tool': tool,
                                 'gold': not tool and len(route) <= fewest + PAR_OVER_FEWEST,
                                 'steps': _json_steps(route)}
    return result


def _try_solve(position):
    """(solve's answer, True), or (None, False) if it couldn't tell."""
    try:
        return solve(position), True
    except SearchLimit:
        return None, False
