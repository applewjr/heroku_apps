"""Design invariants for Thread, the maze programming game in templates/thread.html.

The game is inline JavaScript and this project has no JS runtime, so the maze
generator and rule engine are mirrored here in Python - same seeded PRNG, same
carve/braid/island construction, same rule evaluator - and exercised directly.

These tests exist because the level ladder is a set of claims that are easy to
break by accident and impossible to check by reading:

    levels 1-2  a plain wall-follower must always win
    levels 3-5  a wall-follower must NEVER win, or the levels teach nothing
    every level must be solvable by a program a player can actually write
    level 5     flags must beat no-flags, or three of the sixteen actions are dead weight

Every one of those was violated by a version of this game that looked correct.

The mirror can drift from the template. The first tests below compare the level
table, the shipped preset and the step cap against the real file, so drift in
the parts most likely to move fails loudly rather than silently passing.
"""

import pathlib
import re

import pytest

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "templates" / "thread.html"
SRC = TEMPLATE.read_text(encoding="utf-8")

M32 = 0xFFFFFFFF
DX = [0, 1, 0, -1]          # N, E, S, W
DY = [-1, 0, 1, 0]
OPP = [2, 3, 0, 1]
REL = {"ahead": 0, "right": 1, "back": 2, "left": 3}
STEP_CAP = 2000

# Levels as the mirror understands them. test_level_table_matches_template
# checks this against the template, so an edit there fails here.
LEVELS = [
    {"id": "warmup", "w": 9, "h": 9, "braid": 0, "island": False, "key": False},
    {"id": "long", "w": 15, "h": 15, "braid": 0, "island": False, "key": False},
    {"id": "island", "w": 13, "h": 13, "braid": 0, "island": True, "key": False},
    {"id": "tangle", "w": 15, "h": 15, "braid": 0.6, "island": True, "key": False},
    {"id": "key", "w": 15, "h": 15, "braid": 0.3, "island": True, "key": True},
    {"id": "short", "w": 15, "h": 15, "braid": 0.4, "island": False, "key": False},
]

OPEN_LEVELS = [0, 1, 5]     # a hand on the wall reaches the exit
WALLED_LEVELS = [2, 3, 4]   # the goal is fenced off from the outer wall
KEY_LEVEL = 4
SHORT_LEVEL = 5             # loops, no island: the level where steps are the score

SEEDS = [1] + [(i * 7919 + 13) & 0x7FFFFFFF for i in range(1, 16)]


# --------------------------------------------------------------------------
# mirror of the maze generator
# --------------------------------------------------------------------------

def mulberry32(seed):
    state = [seed & M32]

    def rnd():
        state[0] = (state[0] + 0x6D2B79F5) & M32
        a = state[0]
        t = ((a ^ (a >> 15)) * ((1 | a) & M32)) & M32
        t = ((t + (((t ^ (t >> 7)) * ((61 | t) & M32)) & M32)) & M32) ^ t
        return ((t ^ (t >> 14)) & M32) / 4294967296.0

    return rnd


class Maze:
    def __init__(self, w, h):
        self.W, self.H = w, h
        self.cells = [0b1111] * (w * h)
        self.start = 0
        self.exit = 0
        self.key = -1

    def idx(self, x, y):
        return y * self.W + x

    def cx(self, i):
        return i % self.W

    def cy(self, i):
        return i // self.W

    def inb(self, x, y):
        return 0 <= x < self.W and 0 <= y < self.H

    def is_open(self, i, d):
        return (self.cells[i] & (1 << d)) == 0

    def carve(self, x, y, d):
        nx, ny = x + DX[d], y + DY[d]
        if not self.inb(nx, ny):
            return
        self.cells[self.idx(x, y)] &= ~(1 << d)
        self.cells[self.idx(nx, ny)] &= ~(1 << OPP[d])

    def wall_up(self, x, y, d):
        nx, ny = x + DX[d], y + DY[d]
        self.cells[self.idx(x, y)] |= (1 << d)
        if self.inb(nx, ny):
            self.cells[self.idx(nx, ny)] |= (1 << OPP[d])

    def open_count(self, i):
        return sum(1 for d in range(4) if self.is_open(i, d))


def gen_perfect(m, rand):
    seen = [0] * (m.W * m.H)
    stack = [0]
    seen[0] = 1
    while stack:
        i = stack[-1]
        x, y = m.cx(i), m.cy(i)
        options = [d for d in range(4)
                   if m.inb(x + DX[d], y + DY[d]) and not seen[m.idx(x + DX[d], y + DY[d])]]
        if not options:
            stack.pop()
            continue
        d = options[int(rand() * len(options))]
        m.carve(x, y, d)
        ni = m.idx(x + DX[d], y + DY[d])
        seen[ni] = 1
        stack.append(ni)


def braid(m, rand, p):
    """Remove dead ends, which is what puts loops in the maze."""
    for i in range(m.W * m.H):
        if m.open_count(i) != 1 or rand() > p:
            continue
        x, y = m.cx(i), m.cy(i)
        options = [d for d in range(4)
                   if not m.is_open(i, d) and m.inb(x + DX[d], y + DY[d])]
        if options:
            m.carve(x, y, options[int(rand() * len(options))])


def make_island(m, gx, gy, rand):
    """Open the 3x3 block around a cell, then wall that cell back in on three
    sides. Those three segments then touch nothing else, so they form a wall
    component disconnected from the outer wall - and a follower with one hand
    on the outer wall can never cross onto them."""
    for y in range(gy - 1, gy + 2):
        for x in range(gx - 1, gx + 2):
            for d in range(4):
                nx, ny = x + DX[d], y + DY[d]
                if gx - 1 <= nx <= gx + 1 and gy - 1 <= ny <= gy + 1:
                    m.carve(x, y, d)
    keep = int(rand() * 4)
    for d in range(4):
        if d != keep:
            m.wall_up(gx, gy, d)


def farthest_from(m, src):
    dist = {src: 0}
    queue = [src]
    best = src
    for i in queue:
        if dist[i] > dist[best]:
            best = i
        x, y = m.cx(i), m.cy(i)
        for d in range(4):
            if not m.is_open(i, d):
                continue
            nx, ny = x + DX[d], y + DY[d]
            j = m.idx(nx, ny)
            if m.inb(nx, ny) and j not in dist:
                dist[j] = dist[i] + 1
                queue.append(j)
    return best


def build_maze(level, seed):
    m = Maze(level["w"], level["h"])
    rand = mulberry32(seed)
    gen_perfect(m, rand)
    if level["braid"] > 0:
        braid(m, rand, level["braid"])
    m.start = m.idx(0, 0)
    m.key = -1

    island_cell = -1
    if level["island"]:
        gx = m.W - 3 if level["key"] else m.W // 2
        gy = m.H - 3 if level["key"] else m.H // 2
        make_island(m, gx, gy, rand)
        island_cell = m.idx(gx, gy)

    if level["key"]:
        m.key = island_cell if island_cell >= 0 else farthest_from(m, m.start)
        m.exit = m.start
    elif island_cell >= 0:
        m.exit = island_cell
    else:
        m.exit = m.idx(m.W - 1, m.H - 1)
    return m


# --------------------------------------------------------------------------
# mirror of the rule engine
# --------------------------------------------------------------------------

class St:
    __slots__ = ("x", "y", "facing", "flags", "visits", "cross", "key")


def eval_cond(code, st, m):
    if not code or code == "always":
        return True
    p = code.split(".")
    if p[0] == "flag":
        return st.flags[p[1]] == (p[2] == "on")

    here = m.idx(st.x, st.y)
    if p[0] == "here":
        o = m.open_count(here)
        return {
            "deadend": o == 1,
            "junction": o >= 3,
            "corridor": o == 2,
            "first": st.visits[here] == 1,
            "visited2": st.visits[here] >= 2,
            "visited3": st.visits[here] >= 3,
            "key": m.key >= 0 and here == m.key,
        }[p[1]]

    d = (st.facing + REL[p[0]]) % 4
    nx, ny = st.x + DX[d], st.y + DY[d]
    ok = m.inb(nx, ny)
    trod = st.cross[here * 4 + d]
    return {
        "open": m.is_open(here, d),
        "wall": not m.is_open(here, d),
        "untrodden": trod == 0,
        "trodden1": trod == 1,
        "trodden2": trod >= 2,
        # every predicate that reads the square beyond needs an open way to read
        # it through, never through a wall
        "unvisited": ok and m.is_open(here, d) and st.visits[m.idx(nx, ny)] == 0,
        "exit": ok and m.is_open(here, d) and m.idx(nx, ny) == m.exit,
        "key": ok and m.is_open(here, d) and m.key >= 0 and m.idx(nx, ny) == m.key,
    }[p[1]]


def rule_matches(r, st, m):
    first = eval_cond(r["c1"], st, m)
    if not r["c2"]:
        return first
    if r.get("op") == "or":
        return first or eval_cond(r["c2"], st, m)
    return first and eval_cond(r["c2"], st, m)


def apply_action(code, st, m):
    p = code.split(".")
    if p[0] == "flag":
        st.flags[p[1]] = (not st.flags[p[1]]) if p[2] == "toggle" else (p[2] == "on")
        return
    if p[0] == "turn":
        st.facing = (st.facing + {"right": 1, "around": 2, "left": 3}[p[1]]) % 4
        return

    d = (st.facing + REL[p[1]]) % 4
    st.facing = d                       # a bump still reorients
    here = m.idx(st.x, st.y)
    if not m.is_open(here, d):
        return
    st.x += DX[d]
    st.y += DY[d]
    there = m.idx(st.x, st.y)
    st.cross[here * 4 + d] += 1         # one way, marked from both ends
    st.cross[there * 4 + OPP[d]] += 1
    st.visits[there] += 1


def simulate(m, prog):
    """Returns (outcome, steps). Outcomes: solved, frozen, stuck, norule, cap."""
    active = [r for r in prog if r.get("on", True)]
    st = St()
    st.x, st.y = m.cx(m.start), m.cy(m.start)
    st.facing = 1
    st.flags = {"A": False, "B": False, "C": False}
    st.visits = [0] * (m.W * m.H)
    st.visits[m.start] = 1
    st.cross = [0] * (m.W * m.H * 4)
    st.key = (m.key >= 0 and m.start == m.key)

    stall = max(300, m.W * m.H * 2)
    last_new = 0

    for step in range(1, STEP_CAP + 1):
        chosen = None
        for r in active:
            if rule_matches(r, st, m):
                chosen = r
                break
        if chosen is None:
            return "norule", step

        before = m.idx(st.x, st.y)
        facing_before = st.facing
        flags_before = dict(st.flags)

        apply_action(chosen["act"], st, m)
        if chosen.get("act2"):
            apply_action(chosen["act2"], st, m)   # the rider costs no tick
        after = m.idx(st.x, st.y)
        if m.key >= 0 and after == m.key:
            st.key = True

        # nothing changed, so the same rule fires next tick and forever after
        frozen = (after == before and st.facing == facing_before
                  and st.flags == flags_before)

        if after != before and st.visits[after] == 1:
            last_new = step
        if after == m.exit and (m.key < 0 or st.key):
            return "solved", step
        if frozen:
            return "frozen", step
        if step - last_new >= stall:
            return "stuck", step
    return "cap", STEP_CAP


# --------------------------------------------------------------------------
# programs
# --------------------------------------------------------------------------

def R(c1, act, c2="", op="and", act2=""):
    return {"on": True, "c1": c1, "c2": c2, "op": op, "act": act, "act2": act2}


DIRS = ["right", "ahead", "left", "back"]


def tier(pred, guard=None):
    """One rule per direction, in right/ahead/left/back priority."""
    if guard:
        return [R(guard, "move." + d, d + "." + pred) for d in DIRS]
    return [R(d + ".open", "move." + d, d + "." + pred) for d in DIRS]


WALL_RIGHT = [R("right.open", "move.right"), R("ahead.open", "move.ahead"),
              R("left.open", "move.left"), R("always", "move.back")]

WALL_LEFT = [R("left.open", "move.left"), R("ahead.open", "move.ahead"),
             R("right.open", "move.right"), R("always", "move.back")]

# Tremaux. Rule 1 is the whole trick: re-entering known ground by a way you had
# never walked means you just closed a loop, so turn straight back.
BREADCRUMBS = ([R("here.visited2", "move.back", "back.trodden1")]
               + tier("untrodden") + tier("trodden1")
               + [R("always", "move.back")])

# Same explorer, plus a flag: grab the key, flip, then retrace the singly
# marked ways straight home. The rider sets the flag on the same tick as a move.
OUT_AND_BACK = ([R("here.key", "move.back", "flag.A.off", act2="flag.A.on")]
                + tier("trodden1", guard="flag.A.on")
                + BREADCRUMBS)


def run_all(level_i, prog, seeds=SEEDS):
    out = []
    for s in seeds:
        m = build_maze(LEVELS[level_i], s)
        out.append(simulate(m, prog))
    return out


# --------------------------------------------------------------------------
# the mirror must match the template
# --------------------------------------------------------------------------

def _template_levels():
    block = re.search(r"const LEVELS = \[(.*?)\n    \];", SRC, re.S).group(1)
    found = []
    for chunk in re.findall(r"\{([^{}]*)\}", block):
        def field(name, default=None):
            m = re.search(name + r":\s*([^,\s]+)", chunk)
            return m.group(1).rstrip(",") if m else default
        found.append({
            "id": field("id").strip('"'),
            "w": int(field("w")),
            "h": int(field("h")),
            "braid": float(field("braid")),
            "island": field("island") == "true",
            "key": field("key", "false") == "true",
        })
    return found


def test_level_table_matches_template():
    assert _template_levels() == LEVELS, (
        "templates/thread.html changed its levels; update LEVELS in this file "
        "and re-check the invariants below still hold"
    )


def test_step_cap_matches_template():
    cap = int(re.search(r"const STEP_CAP = (\d+);", SRC).group(1))
    assert cap == STEP_CAP


def test_elements_toggled_by_the_hidden_attribute_really_hide():
    """Any author `display` rule beats the browser's own `[hidden]{display:none}`,
    so an element with a display rule stays visible however often script sets
    its hidden attribute. It needs an explicit `[hidden]` override."""
    style = re.search(r"<style>(.*?)</style>", SRC, re.S).group(1)
    checked = 0
    for tag in re.findall(r"<[a-z]+[^>]*\shidden[\s>]", SRC):
        found = re.search(r'class="([^"]*)"', tag)
        if not found:
            continue
        for cls in found.group(1).split():
            if re.search(r"\." + re.escape(cls) + r"\s*\{[^}]*\bdisplay\s*:", style):
                checked += 1
                assert re.search(r"\." + re.escape(cls) + r"\[hidden\]", style), (
                    f".{cls} sets display and is toggled by the hidden attribute, "
                    f"but has no .{cls}[hidden] override - it will never hide"
                )
    assert checked, "expected at least one hidden-toggled element to verify"


def test_breadcrumbs_preset_matches_template():
    block = re.search(r"breadcrumbs: \[(.*?)\n        \],", SRC, re.S).group(1)
    shipped = re.findall(r'c1: "([^"]*)", c2: "([^"]*)", act: "([^"]*)"', block)
    mirrored = [(r["c1"], r["c2"], r["act"]) for r in BREADCRUMBS]
    assert shipped == mirrored, "the shipped solution preset drifted from the mirror"


# --------------------------------------------------------------------------
# the mazes themselves
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level_i", range(len(LEVELS)))
def test_every_square_is_reachable(level_i):
    for seed in SEEDS:
        m = build_maze(LEVELS[level_i], seed)
        seen = {m.start}
        stack = [m.start]
        while stack:
            i = stack.pop()
            x, y = m.cx(i), m.cy(i)
            for d in range(4):
                if not m.is_open(i, d):
                    continue
                nx, ny = x + DX[d], y + DY[d]
                j = m.idx(nx, ny)
                if m.inb(nx, ny) and j not in seen:
                    seen.add(j)
                    stack.append(j)
        assert len(seen) == m.W * m.H, f"{LEVELS[level_i]['id']} seed {seed} has cut-off squares"


@pytest.mark.parametrize("level_i", range(len(LEVELS)))
def test_outer_border_is_sealed(level_i):
    for seed in SEEDS:
        m = build_maze(LEVELS[level_i], seed)
        for x in range(m.W):
            assert not m.is_open(m.idx(x, 0), 0)
            assert not m.is_open(m.idx(x, m.H - 1), 2)
        for y in range(m.H):
            assert not m.is_open(m.idx(0, y), 3)
            assert not m.is_open(m.idx(m.W - 1, y), 1)


# --------------------------------------------------------------------------
# the ladder
# --------------------------------------------------------------------------

@pytest.mark.parametrize("level_i", OPEN_LEVELS)
def test_wall_following_always_wins_the_open_levels(level_i):
    """Levels 1-2 exist to teach the tool, so the starting preset must work."""
    for prog, name in ((WALL_RIGHT, "right"), (WALL_LEFT, "left")):
        outcomes = [o for o, _ in run_all(level_i, prog)]
        assert set(outcomes) == {"solved"}, f"{name}-hand rule failed a plain maze: {outcomes}"


@pytest.mark.parametrize("level_i", WALLED_LEVELS)
def test_wall_following_never_wins_the_walled_levels(level_i):
    """The island is the entire point of levels 3-5. If a hand on the wall can
    reach the goal even once, the level has stopped teaching anything."""
    for prog, name in ((WALL_RIGHT, "right"), (WALL_LEFT, "left")):
        outcomes = [o for o, _ in run_all(level_i, prog)]
        assert "solved" not in outcomes, f"{name}-hand rule solved a walled level"


@pytest.mark.parametrize("level_i", range(len(LEVELS)))
def test_breadcrumbs_solves_every_level(level_i):
    """Every level needs at least one program a player can actually write."""
    results = run_all(level_i, BREADCRUMBS)
    bad = [(o, s) for o, s in results if o != "solved"]
    assert not bad, f"{LEVELS[level_i]['id']} is not always solvable: {bad}"


def test_flags_pay_for_themselves_on_the_key_level():
    """Three of the sixteen actions are flag operations. If carrying state is
    not worth its rules, they are decoration."""
    flagless = [s for o, s in run_all(KEY_LEVEL, BREADCRUMBS) if o == "solved"]
    flagged = [s for o, s in run_all(KEY_LEVEL, OUT_AND_BACK) if o == "solved"]

    assert len(flagged) == len(SEEDS), "the flag program must solve every key maze"
    assert len(flagless) == len(SEEDS)

    saving = 1 - (sum(flagged) / len(flagged)) / (sum(flagless) / len(flagless))
    assert saving > 0.10, f"flags only saved {saving:.0%} of the steps, not worth the rules"


def test_the_short_way_rewards_a_better_program():
    """Level 6 only earns its place if different programs get meaningfully
    different step counts on the same maze. That is what makes "fewest steps"
    a thing a player can chase rather than a number they are handed."""
    results = {}
    for name, prog in (("right", WALL_RIGHT), ("left", WALL_LEFT), ("crumbs", BREADCRUMBS)):
        out = run_all(SHORT_LEVEL, prog)
        assert all(o == "solved" for o, _ in out), f"{name} failed to solve level 6"
        results[name] = [steps for _, steps in out]

    per_maze = list(zip(*results.values()))

    # The sharp test. On a maze with no loops, "prefer untrodden" and "hand on
    # the wall" walk byte-identical routes, so memory is worth exactly nothing
    # and there is no craft to reward. Loops are the only thing that breaks it.
    moved = sum(1 for a, b in zip(results["right"], results["crumbs"]) if a != b)
    assert moved > len(SEEDS) * 0.35, (
        f"carrying memory changed the step count on only {moved}/{len(SEEDS)} mazes - "
        f"this level is not braided enough to reward a better program"
    )

    spread = sum(max(row) - min(row) for row in per_maze) / len(per_maze)
    mean = sum(sum(row) for row in per_maze) / (len(per_maze) * len(results))
    assert spread / mean > 0.25, (
        f"best and worst program differ by only {spread / mean:.0%} of a run - "
        f"too flat to be worth tuning"
    )


def test_a_maze_without_loops_gives_nothing_to_optimise():
    """The other half of that claim, and the reason level 6 has to be braided.
    On a maze with no loops, "prefer untrodden ways" and "keep a hand on the
    wall" walk the identical route, step for step - so all the extra machinery
    buys exactly nothing and the step score is decoration."""
    right = [steps for o, steps in run_all(1, WALL_RIGHT) if o == "solved"]
    crumbs = [steps for o, steps in run_all(1, BREADCRUMBS) if o == "solved"]
    assert right == crumbs, "a loopless maze unexpectedly separated these two programs"


# --------------------------------------------------------------------------
# the rule language
# --------------------------------------------------------------------------

def test_or_is_exactly_two_adjacent_rows():
    """OR is convenience, not power. If it ever stops being equal to the two
    rows it stands for, it has grown semantics nobody designed."""
    with_or = [R("right.open", "move.right", "left.open", op="or"), R("always", "move.back")]
    expanded = [R("right.open", "move.right"), R("left.open", "move.right"),
                R("always", "move.back")]
    for level_i in range(len(LEVELS)):
        for seed in SEEDS[:6]:
            m = build_maze(LEVELS[level_i], seed)
            assert simulate(m, with_or) == simulate(m, expanded)


@pytest.mark.parametrize("level_i", range(len(LEVELS)))
def test_nothing_is_ever_sensed_through_a_wall(level_i):
    """A bot that can sense the exit through a wall will walk into that wall
    forever, because the condition never stops being true.

    `unvisited` is the same promise and used to break it: it answered about the
    square behind a wall, so a bot could be told it had already stood somewhere
    it had no way into. That contradicts the one rule the whole game rests on,
    and it made `IF ahead unvisited THEN move ahead` - the most natural first
    rule anyone writes - a silent wall-bump."""
    for seed in SEEDS[:6]:
        m = build_maze(LEVELS[level_i], seed)
        st = St()
        st.flags = {"A": False, "B": False, "C": False}
        st.cross = [0] * (m.W * m.H * 4)
        st.key = False
        # every square already stood on, so "unvisited" has something to leak
        st.visits = [1] * (m.W * m.H)
        for i in range(m.W * m.H):
            for d in range(4):
                if m.is_open(i, d):
                    continue                       # only walled directions matter
                st.x, st.y, st.facing = m.cx(i), m.cy(i), d
                assert not eval_cond("ahead.exit", st, m)
                assert not eval_cond("ahead.key", st, m)
                # false either way round: the square beyond a wall is
                # unreadable, not unvisited, so the answer must not move when
                # the square behind that wall does
                assert not eval_cond("ahead.unvisited", st, m)
                nx, ny = m.cx(i) + DX[d], m.cy(i) + DY[d]
                if not m.inb(nx, ny):
                    continue
                j = m.idx(nx, ny)
                st.visits[j] = 0
                assert not eval_cond("ahead.unvisited", st, m)
                st.visits[j] = 1


def test_a_program_with_no_catch_all_halts_rather_than_hanging():
    """Running out of matching rules must be reported, not spun on."""
    m = build_maze(LEVELS[0], 1)
    outcome, _ = simulate(m, [R("here.junction", "move.ahead")])
    assert outcome in ("norule", "stuck", "frozen")


def test_a_bot_that_cannot_change_anything_halts_at_once():
    """The program a new player is handed is a single rule that walks into a
    wall. Discovering that by burning the whole step budget would be a terrible
    first run, so a fixed point has to be caught the moment it happens."""
    m = build_maze(LEVELS[0], 1)
    outcome, steps = simulate(m, [R("always", "move.ahead")])
    assert outcome == "frozen"
    assert steps < 20, f"took {steps} steps to notice it was stuck against a wall"


def test_the_shipped_presets_never_freeze():
    """Every preset ends in a catch-all that turns the bot around, so none of
    them can wedge against a wall."""
    for name, prog in (("right", WALL_RIGHT), ("left", WALL_LEFT),
                       ("solution", BREADCRUMBS), ("out and back", OUT_AND_BACK)):
        for level_i in range(len(LEVELS)):
            for seed in SEEDS[:4]:
                outcome, _ = simulate(build_maze(LEVELS[level_i], seed), prog)
                assert outcome != "frozen", f"{name} froze on level {level_i + 1}"
