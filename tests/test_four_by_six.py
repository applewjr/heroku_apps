"""The 4x6 solver (functions/four_by_six.py): the rules, the search against a
plain breadth-first search, boards from partway through a game, the Extra
pocket, the game's own last move, the rainbow route, points, and what the
page gets. Every board here is made up (seeded shuffles), never a real
puzzle's."""

import random
from collections import deque

import pytest

from functions import four_by_six as fbs

SEED_BOARDS = 40
DONE = ('YYYY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGGG', '', '')


def deal(rng):
    tiles = list(fbs.COLORS * 4)
    rng.shuffle(tiles)
    return fbs.parse_board(''.join(tiles))


def brute_fewest(position):
    """The fewest moves by breadth-first search with no estimate to be
    wrong, under the same rules: no tile into Extra, a finished board ends
    the game, and the game's own last move is the only one where it applies."""
    seen = {fbs._same_shelves(position)}
    queue = deque([(position, 0)])
    while queue:
        now, made = queue.popleft()
        if fbs.is_done(now):
            return made
        forced = fbs.auto_finish(now)
        for src, dst, _n in [forced] if forced else fbs.legal_moves(now):
            after = fbs.apply_move(now, src, dst)
            key = fbs._same_shelves(after)
            if key not in seen:
                seen.add(key)
                queue.append((after, made + 1))
    return None


def replay(position, steps):
    """The position after the steps, each checked as the game would."""
    for step in steps:
        if step.src == fbs.EXTRA:
            assert step.n == 1 and fbs.holding(position) == step.color
        elif step.dst == fbs.EXTRA:
            assert step.n == 1 and position[fbs.EXTRA] == '' and position[step.src][-1] == step.color
        else:
            source, target = position[step.src], position[step.dst]
            assert source and source[-step.n:] == step.color * step.n
            assert len(target) + step.n <= 4 and (not target or target[-1] == step.color)
            # The whole run goes, or as much of it as fits.
            assert step.n == min(fbs.run_length(source), 4 - len(target))
        position = fbs.apply_move(position, step.src, step.dst)
        assert position == step.after
    return position


@pytest.fixture(scope='module')
def dealt():
    rng = random.Random(20261123)
    return [deal(rng) for _ in range(400)]


@pytest.fixture(scope='module')
def solvable(dealt):
    out = [b for b in dealt if fbs.solve(b) is not None]
    assert len(out) >= SEED_BOARDS
    return out


def partway(board, rng, moves):
    """The position after `moves` random legal moves (fewer at a dead end)."""
    position = board
    for _ in range(moves):
        options = fbs.legal_moves(position)
        if not options:
            break
        src, dst, _n = rng.choice(options)
        position = fbs.apply_move(position, src, dst)
    return position


# --- boards -----------------------------------------------------------------------------

def test_a_starting_board_is_24_tiles_four_of_each_color():
    board = fbs.parse_board('yyyyookkppbbggggookkppbb')
    assert board == ('YYYY', 'OOKK', 'PPBB', 'GGGG', 'OOKK', 'PPBB', '', '')
    assert fbs.is_start(board) and fbs.board_text(board) == 'YYYYOOKKPPBBGGGGOOKKPPBB'


def test_a_board_from_partway_through_a_game():
    board = fbs.parse_board('Y|OOKK|PPBB|GGGG|OOK|PPBB|YYY|K')
    assert board == ('Y', 'OOKK', 'PPBB', 'GGGG', 'OOK', 'PPBB', 'YYY', 'K')
    assert not fbs.is_start(board) and fbs.board_text(board) == 'Y|OOKK|PPBB|GGGG|OOK|PPBB|YYY|K'
    # Full shelves written in parts are a starting board.
    assert fbs.board_text(fbs.parse_board('YYYY|OOKK|PPBB|GGGG|OOKK|PPBB||')) == 'YYYYOOKKPPBBGGGGOOKKPPBB'


@pytest.mark.parametrize('bad', [None, 24, [], '', 'YYYY', 'Y' * 25, 'YYYYOOKKPPBBGGGGOOKKPPBX',
                                 'YYYYYOKKPPBBGGGGOOKKPPBB', 'YYYY|OOKK|PPBB|GGGG|OOKK|PPBB|',
                                 'YYYY|OOKK|PPBB|GGGG|OOKK|PPBB|||', 'YYYYY|OKK|PPBB|GGGG|OOKK|PPBB||',
                                 'YYY|OOKK|PPBB|GGGG|OOK|PPBB|YK|Y', 'YY|OOKK|PPBB|GGGG|OOK|PPBB|YY|KK',
                                 'YY|OOKK|PPBB|GGGG|OOKK|PPBB||YY', 'YYY|OOKK|PPBB|GGGG|OOK|PPBB|YYX|K'])
def test_anything_else_is_not_a_board(bad):
    with pytest.raises(fbs.BoardError):
        fbs.parse_board(bad)


def test_the_errors_say_what_to_check():
    with pytest.raises(fbs.BoardError, match='yellow, orange'):
        fbs.parse_board('YYYYYOKKPPBBGGGGOOKKPPBB')
    with pytest.raises(fbs.BoardError, match='one color at a time'):
        fbs.parse_board('YYY|OOKK|PPBB|GGGG|OOK|PPBB|YK|')


# --- moves ------------------------------------------------------------------------------

def test_a_move_takes_the_whole_run():
    position = ('YOO', 'KO', 'KKK', 'PPPP', 'BBBB', 'GGGG', '', '')
    assert fbs.apply_move(position, 0, 1) == ('Y', 'KOOO', 'KKK', 'PPPP', 'BBBB', 'GGGG', '', '')


def test_as_much_of_a_run_as_fits():
    position = ('YOO', 'KPO', 'KKK', 'PPP', 'BBBB', 'GGGG', '', '')
    assert fbs.apply_move(position, 0, 1) == ('YO', 'KPOO', 'KKK', 'PPP', 'BBBB', 'GGGG', '', '')


def test_a_move_needs_room_and_the_same_color():
    position = ('YO', 'KP', 'KKKK', '', 'BBBB', 'GGGG', 'O', '')
    for src, dst in ((0, 1), (1, 0), (0, 2), (3, 0), (0, 0), (1, 6)):
        assert fbs.moved(position, src, dst) == 0
        with pytest.raises(fbs.BoardError):
            fbs.apply_move(position, src, dst)
    assert fbs.moved(position, 0, 3) == 1      # an empty shelf takes anything
    assert fbs.moved(position, 0, 6) == 1      # the Stage holds the same color
    assert fbs.moved(position, 6, 0) == 1


def test_extra_takes_one_tile_once():
    position = ('YOO', 'KO', 'KKK', 'PPPP', 'BBBB', 'GGGG', '', '')
    assert fbs.moved(position, 0, fbs.EXTRA) == 1           # just the top tile, not the run
    held = fbs.apply_move(position, 0, fbs.EXTRA)
    assert held == ('YO', 'KO', 'KKK', 'PPPP', 'BBBB', 'GGGG', '', 'O')
    assert fbs.moved(held, 1, fbs.EXTRA) == 0               # full
    assert fbs.moved(held, fbs.EXTRA, 2) == 0               # not next to pink
    back = fbs.apply_move(held, fbs.EXTRA, 1)
    assert back == ('YO', 'KOO', 'KKK', 'PPPP', 'BBBB', 'GGGG', '', fbs.SPENT)
    assert fbs.moved(back, 0, fbs.EXTRA) == 0               # used up
    assert fbs.moved(back, fbs.EXTRA, 0) == 0               # nothing in it
    # The tool is never part of the fewest moves.
    assert all(dst != fbs.EXTRA for _src, dst, _n in fbs.legal_moves(position))
    assert any(dst == fbs.EXTRA for _src, dst, _n in fbs.legal_moves(position, tool=True))


@pytest.mark.parametrize('src,dst', [(-1, 0), (0, 8), ('0', 1), (None, 1), (0.0, 1), (True, 0)])
def test_a_move_names_two_places(src, dst):
    with pytest.raises(fbs.BoardError):
        fbs.apply_move(('YO', 'KP', '', '', '', '', '', ''), src, dst)


def test_done_needs_an_empty_stage_and_nothing_held():
    assert fbs.is_done(DONE) and fbs.is_done(DONE[:7] + (fbs.SPENT,))
    assert not fbs.is_done(('YYYY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', '', 'GGGG', ''))
    assert not fbs.is_done(('YYYO', 'OOOY', 'KKKK', 'PPPP', 'BBBB', 'GGGG', '', ''))
    assert not fbs.is_done(('YYYY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGG', '', 'G'))


def test_the_game_makes_the_last_move_off_the_stage_itself():
    stage_full = ('', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGGG', 'YYYY', '')
    assert fbs.auto_finish(stage_full) == (fbs.STAGE, 0, 4)
    assert fbs.auto_finish(('YYY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGGG', 'Y', '')) == (fbs.STAGE, 0, 1)
    # Not while a tile is held in Extra, nor when the move wouldn't finish.
    assert fbs.auto_finish(('YYY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGG', 'Y', 'G')) is None
    assert fbs.auto_finish(('YYY', 'OOOO', 'KKKK', 'PPPP', 'BBB', 'GGGG', 'Y', 'B')) is None
    assert fbs.auto_finish(('YY', 'OOOO', 'KKKK', 'PPPP', 'BBBB', 'GGGG', 'Y', 'Y')) is None
    assert fbs.auto_finish(DONE) is None


# --- the search -------------------------------------------------------------------------

def test_the_search_finds_the_fewest_moves(dealt):
    for board in dealt[:150]:
        steps = fbs.solve(board)
        assert (None if steps is None else len(steps)) == brute_fewest(board), board


def test_every_solution_plays_out(solvable):
    for board in solvable:
        assert fbs.is_done(replay(board, fbs.solve(board)))


def test_from_partway_too_dead_ends_and_all(solvable):
    rng = random.Random(6)
    dead = 0
    for board in solvable[:SEED_BOARDS]:
        position = partway(board, rng, rng.randint(1, 8))
        steps = fbs.solve(position)
        fewest = brute_fewest(position)
        assert (None if steps is None else len(steps)) == fewest
        dead += fewest is None
        # Entered by a player, the same position solves the same.
        assert fbs.solve(fbs.parse_board(fbs.board_text(position))) == steps
    assert dead > 0


def test_a_tile_held_in_extra_goes_back(solvable):
    rng = random.Random(8)
    for board in solvable[:SEED_BOARDS]:
        position = partway(board, rng, rng.randint(0, 4))
        src = next((i for i in range(fbs.EXTRA) if position[i]), None)
        held = fbs.apply_move(position, src, fbs.EXTRA)
        steps = fbs.solve(held)
        assert (None if steps is None else len(steps)) == brute_fewest(held)
        if steps:
            assert sum(s.src == fbs.EXTRA for s in steps) == 1 and fbs.is_done(replay(held, steps))


def test_the_estimate_is_never_too_high(solvable):
    for board in solvable[:SEED_BOARDS]:
        steps = fbs.solve(board)
        for made, position in enumerate([board] + [s.after for s in steps]):
            assert fbs.at_least(position) <= len(steps) - made


def test_rainbow_finishes_in_rainbow_order_as_the_game_plays(solvable):
    ups = set()
    for board in solvable[:SEED_BOARDS]:
        route = fbs.rainbow(board)
        position = board
        for k, step in enumerate(route):
            # Never through a finished board, and never where the game would
            # make another move by itself.
            forced = fbs.auto_finish(position)
            assert forced is None or forced == (step.src, step.dst, step.n)
            assert not fbs.is_done(position)
            position = replay(position, [step])
        order = ''.join(s[0] for s in position[:6])
        assert fbs.is_done(position) and order in (fbs.COLORS, fbs.COLORS[::-1])
        assert sum(s.dst == fbs.EXTRA for s in route) <= 1
        assert len(route) >= len(fbs.solve(board))
        ups.add(order)
    assert len(ups) == 2


def test_rainbow_avoids_the_games_own_last_move():
    # Blue on the Stage, shelf 1 empty, the rest sorted: 4x6 would finish
    # the board itself, out of rainbow order, so a route can't pass here.
    trap = ('', 'PPPP', 'YYYY', 'GGGG', 'OOOO', 'KKKK', 'BBBB', '')
    assert fbs.auto_finish(trap) == (fbs.STAGE, 0, 4)
    assert fbs._search(trap, lambda p: p, lambda p: 0,
                       lambda p: fbs.is_done(p) and ''.join(s[0] for s in p[:6]) == fbs.COLORS) is None


def test_a_finished_board_needs_no_moves():
    assert fbs.solve(DONE) == () and fbs.rainbow(DONE) == ()


def test_the_cap_stops_a_search(monkeypatch, solvable):
    monkeypatch.setattr(fbs, 'POSITION_CAP', 2)
    assert fbs._search(solvable[0], fbs._same_shelves, fbs.at_least, fbs.is_done) is None


# --- points -----------------------------------------------------------------------------

@pytest.mark.parametrize('weekday,best', [(0, 325), (1, 330), (2, 335), (3, 340), (4, 345), (5, 350), (6, 355)])
def test_the_fewest_moves_with_no_tool_or_undo_score_the_most(weekday, best):
    assert fbs.points(weekday, 13, 13) == best
    for tools in range(4):
        for undos in (0, 1):
            # A tool could only ever save moves below the fewest.
            for moves in range(1 if tools else 13, 25):
                assert fbs.points(weekday, moves, 13, undos, tools) <= best


def test_the_streak_adds_up_to_fifty():
    assert fbs.points(0, 13, 13, streak=1) == 326
    assert fbs.points(0, 13, 13, streak=50) == fbs.points(0, 13, 13, streak=400) == 375


@pytest.mark.parametrize('total,short', [(13, 0), (14, 50), (15, 65), (16, 75), (20, 75)])
def test_what_extra_moves_cost(total, short):
    assert fbs.short_of_best(total, 13) == short


# --- what the page gets -----------------------------------------------------------------

def test_the_plan_from_the_start(solvable):
    board = solvable[0]
    steps = fbs.solve(board)
    plan = fbs.plan(board)
    assert plan['status'] == 'start' and plan['solvable'] and plan['made'] == 0 and plan['start_board']
    assert plan['fewest'] == plan['left'] == plan['total'] == len(plan['steps']) == len(steps)
    assert plan['par'] == len(steps) + 2 and plan['short'] == 0 and plan['gold']
    assert set(plan['homes']) == set(fbs.COLORS) and sorted(plan['homes'].values()) == list(range(6))
    assert plan['steps'][-1]['after'][6] == '' and len(plan['position']) == 8
    assert plan['stage_colors'] and len(set(plan['stage_colors'])) == len(plan['stage_colors'])
    # The rainbow route only when asked for.
    assert 'rainbow' not in plan
    route = fbs.plan(board, with_rainbow=True)['rainbow']
    assert route['moves'] == len(route['steps']) and route['tool'] == any(s['to'] == fbs.EXTRA for s in route['steps'])
    assert not (route['tool'] and route['gold'])


def test_the_plan_follows_the_players_moves(solvable):
    board = solvable[0]
    steps = fbs.solve(board)
    made = [(s.src, s.dst) for s in steps]
    for n in (1, 5, len(steps) - 1):
        plan = fbs.plan(board, made[:n], with_rainbow=True)
        assert plan['status'] == 'on_track' and plan['left'] == len(steps) - n
        assert plan['total'] == plan['fewest'] and 'rainbow' not in plan
    plan = fbs.plan(board, made)
    assert plan['status'] == 'done' and plan['left'] == 0 and plan['steps'] == []
    assert set(plan['homes']) == set(fbs.COLORS)


def test_a_plan_for_a_board_from_partway_through(solvable):
    board = fbs.apply_move(solvable[0], *[(s.src, s.dst) for s in fbs.solve(solvable[0])][0])
    plan = fbs.plan(fbs.parse_board(fbs.board_text(board)), with_rainbow=True)
    assert plan['start_board'] is False and plan['status'] == 'start' and 'rainbow' not in plan
    assert plan['fewest'] == len(fbs.solve(solvable[0])) - 1


def test_off_track_and_dead_ends_are_told_apart(solvable):
    off = dead = None
    for board in solvable:
        for src, dst, _n in fbs.legal_moves(board):
            plan = fbs.plan(board, [(src, dst)])
            if plan['status'] == 'off':
                off = off or plan
            elif plan['status'] == 'dead_end':
                dead = dead or plan
        if off and dead:
            break
    assert off['total'] > off['fewest'] and off['short'] >= 50 and off['steps']
    assert dead['steps'] == [] and dead['solvable']


def test_a_player_may_use_extra_once(solvable):
    board = solvable[0]
    plan = fbs.plan(board, [(0, fbs.EXTRA)])
    assert plan['position'][fbs.EXTRA] and plan['status'] in ('off', 'dead_end')
    with pytest.raises(fbs.BoardError):
        fbs.plan(board, [(0, fbs.EXTRA), (1, fbs.EXTRA)])


def test_a_board_with_no_solution(dealt):
    board = next(b for b in dealt if fbs.solve(b) is None)
    plan = fbs.plan(board)
    assert plan == {'solvable': False, 'made': 0, 'position': list(board), 'start_board': True, 'status': 'unsolvable'}


def test_an_illegal_move_is_refused(solvable):
    with pytest.raises(fbs.BoardError):
        fbs.plan(solvable[0], [(6, 0)])
