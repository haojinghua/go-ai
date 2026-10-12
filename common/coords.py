"""围棋坐标工具（GTP 坐标系，列字母跳过 I）。对弈/分析两条业务线共用，只含纯函数。"""
import re

COLS = 'ABCDEFGHJKLMNOPQRST'  # GTP 列标签（跳过 I）
GTP_COORD_RE = re.compile(r"^([A-HJ-Z])([1-9][0-9]*)$")


def gtp_to_rc(pos, board_size):
    """GTP 坐标 -> (row, col)（0 基，行 0 为棋盘顶部）；非法或越界返回 None"""
    m = GTP_COORD_RE.match(pos)
    if not m:
        return None
    col_letter = m.group(1)
    if col_letter not in COLS:
        return None
    col = COLS.index(col_letter)
    row = board_size - int(m.group(2))
    if not (0 <= row < board_size):
        return None
    return (row, col)


def rc_to_gtp(rc, board_size):
    """(row, col) -> GTP 坐标"""
    return COLS[rc[1]] + str(board_size - rc[0])
