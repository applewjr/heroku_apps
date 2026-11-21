-- Circuits today's boards: room for boards of any size up to 8 by 8 (routes/
-- circuits.py, functions/circuits.py MAX_SIDE). Boards no longer come in a
-- fixed list of sizes: players grow the grid with + at its edges, and each
-- board is stored as the squares it uses. The columns below were sized for
-- small boards; an 8 by 8 board can need, at most:
--   cells       64 cells of up to 24 characters, '|'-joined:   1,599
--   arrows      112 'tail-head' pairs of up to 5, comma-joined:   671
--   answers, entered, solver_top  64 words of up to 24:          1,599
-- (A realistic dense 8 by 8 already needs 246 of arrows' 255.)
--
-- Run this BEFORE deploying the code that lets boards grow. Each column only
-- gets longer, in place: the table is utf8mb4, so these VARCHARs keep the
-- 2-byte length they already have and no table copy is needed. LOCK=NONE and
-- ALGORITHM=INPLACE make MySQL refuse, rather than copy the table, if it ever
-- couldn't. Nothing else is touched: safe to run live. Prod and staging share
-- the JawsDB; both are fine.
--
-- Code deployed without it fails safe: boards that fit the old columns (every
-- real board so far, and realistic ones up to about 6 by 6) work as before; a
-- bigger one isn't stored, behind a `circuits_board_log_failed` alert, and
-- approving one says "Database error".

SET SESSION lock_wait_timeout = 5;

ALTER TABLE circuits_boards
    MODIFY cells       VARCHAR(2000) NOT NULL,
    MODIFY arrows      VARCHAR(1000) NOT NULL,
    MODIFY answers     VARCHAR(2000) NULL,
    MODIFY entered     VARCHAR(2000) NULL,
    MODIFY solver_top  VARCHAR(2000) NULL,
    ALGORITHM=INPLACE, LOCK=NONE;


-- ---------------------------------------------------------------------------
-- Rollback: none needed. The previous code reads and writes the longer
-- columns as it did the shorter ones.
