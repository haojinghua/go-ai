"""SGF 棋谱解析（纯函数，两条业务线均可选用）。从 app.py 原样搬迁。"""
from common.coords import COLS

SGF_GTP_COLS = COLS  # GTP 列字母（跳过 I）


def parse_sgf(sgf_text):
    """
    解析 SGF 围棋棋谱，提取主线的对局信息、摆子(AB/AW)和着法序列。

    支持：变化图（只取第一个分支为主线）、pass（空值或 tt）、转义字符。
    返回:
        {
            "status": "ok",
            "board_size": int, "komi": float,
            "meta": {对局信息 PB/PW/RE/DT/EV 等},
            "setup_stones": [["B","D4"], ...],        # AB/AW 摆子（GTP 坐标）
            "moves": [["B","Q16"],["W","pass"], ...],  # 主线着法（GTP 坐标或 pass）
            "move_count": int
        }
    """
    text = sgf_text
    n = len(text)
    start = text.find('(')
    if start < 0:
        return {"status": "error", "message": "不是有效的 SGF 文件（未找到棋谱树）"}

    state = {
        "board_size": 19,
        "komi": 6.5,
        "meta": {},
        "setup_stones": [],
        "moves": [],
        "is_go": None,
    }

    def coord_to_gtp(v):
        """SGF 坐标转 GTP 坐标；空值或 tt(19路及以下) 视为 pass"""
        size = state["board_size"]
        if not v:
            return "pass"
        if v == "tt" and size <= 19:
            return "pass"
        if len(v) != 2:
            return None
        col = ord(v[0]) - 97
        row = ord(v[1]) - 97
        if not (0 <= col < size and 0 <= row < size) or col >= len(SGF_GTP_COLS):
            return None
        return SGF_GTP_COLS[col] + str(size - row)

    def handle_property(ident, vals):
        if not vals:
            return
        if ident == "SZ":
            try:
                sz = int(vals[0])
                state["board_size"] = sz
            except (ValueError, IndexError):
                pass
        elif ident == "GM":
            try:
                state["is_go"] = (int(vals[0]) == 1)
            except (ValueError, IndexError):
                pass
        elif ident == "KM":
            try:
                state["komi"] = float(vals[0])
            except (ValueError, IndexError):
                pass
        elif ident in ("PB", "PW", "BR", "WR", "RE", "DT", "EV", "GN", "PC", "CP", "US", "HA"):
            state["meta"][ident] = vals[0][:80]
        elif ident in ("AB", "AW"):
            color = "B" if ident == "AB" else "W"
            for v in vals:
                gtp = coord_to_gtp(v)
                if gtp is not None:
                    state["setup_stones"].append([color, gtp])
        elif ident in ("B", "W"):
            for v in vals:
                gtp = coord_to_gtp(v)
                if gtp is None:
                    continue  # 非法坐标，跳过
                state["moves"].append([ident, gtp])
        # 其余属性（注释、标记等）忽略

    def skip_ws(p):
        while p < n and text[p] in " \t\r\n":
            p += 1
        return p

    def read_values(p):
        """读取连续的 [...] 属性值（p 位于第一个 '[' 之前）"""
        vals = []
        p = skip_ws(p)
        while p < n and text[p] == '[':
            p += 1
            buf = []
            while p < n:
                c = text[p]
                if c == '\\' and p + 1 < n:
                    buf.append(text[p + 1])
                    p += 2
                    continue
                if c == ']':
                    p += 1
                    break
                buf.append(c)
                p += 1
            vals.append(''.join(buf))
            p = skip_ws(p)
        return vals, p

    def skip_tree(p):
        """跳过一整个子树（p 位于 '(' 之后），返回其 ')' 之后的位置"""
        depth = 1
        while p < n and depth > 0:
            c = text[p]
            if c == '\\':
                p += 2
                continue
            if c == '[':
                p += 1
                while p < n:
                    if text[p] == '\\':
                        p += 2
                        continue
                    if text[p] == ']':
                        break
                    p += 1
                if p < n:
                    p += 1
                continue
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            p += 1
        return p

    def parse_tree_body(p, collect):
        """
        解析一个子树，p 位于 '(' 之后，返回其 ')' 之后的位置。
        主线策略：本树节点全部收集；遇到子变化图时只递归收集第一个，其余跳过。
        """
        while p < n:
            p = skip_ws(p)
            if p >= n:
                break
            c = text[p]
            if c == ';':
                # 节点：连续读取属性
                p += 1
                while True:
                    p = skip_ws(p)
                    if p >= n:
                        break
                    c2 = text[p]
                    if c2.isalpha():
                        s = p
                        while p < n and text[p].isalpha():
                            p += 1
                        ident = text[s:p]
                        vals, p = read_values(p)
                        if collect and vals:
                            handle_property(ident, vals)
                    else:
                        break
            elif c == '(':
                # 变化图：只收集第一个分支（主线），其余整树跳过
                p = parse_tree_body(p + 1, collect)
                while True:
                    p = skip_ws(p)
                    if p < n and text[p] == '(':
                        p = skip_tree(p + 1)
                    else:
                        break
                p = skip_ws(p)
                if p < n and text[p] == ')':
                    p += 1
                return p
            elif c == ')':
                p += 1
                return p
            else:
                p += 1  # 容错：跳过无法识别的字符
        return p

    parse_tree_body(start + 1, True)

    if state["is_go"] is False:
        return {"status": "error", "message": "这不是围棋棋谱（GM 不是 1）"}
    if not (2 <= state["board_size"] <= 19):
        return {"status": "error", "message": "不支持的棋盘大小 %d（仅支持 2-19 路）" % state["board_size"]}
    if not state["moves"] and not state["setup_stones"]:
        return {"status": "error", "message": "棋谱中没有找到任何着法或摆子"}

    return {
        "status": "ok",
        "board_size": state["board_size"],
        "komi": state["komi"],
        "meta": state["meta"],
        "setup_stones": state["setup_stones"],
        "moves": state["moves"],
        "move_count": len(state["moves"]),
    }
