"""
围棋棋盘截图识别模块
从截图中自动识别黑子和白子的位置

核心算法:
1. 网格标定: 优先用HoughLines检测格线(RANSAC式选出最佳19条等间距线),
   备选用霍夫圆检测棋子位置反推格距和起点
2. 棋子分类: HSV饱和度 + 亮度双判据
   - 黑子: 中心亮度 < 130 且 饱和度 < 75
   - 白子: 中心亮度 > 140 且 饱和度 < 55
   - 空位: 棋盘木纹背景饱和度 > 80

关键洞察: 木纹棋盘是黄褐色(高饱和度~90), 而黑白棋子都是无彩色(低饱和度<55)
"""
import cv2
import numpy as np
import base64


COLS_LABEL = 'ABCDEFGHJKLMNOPQRST'


def decode_image(image_data):
    """从 base64 或二进制数据解码图片"""
    if isinstance(image_data, str):
        if ',' in image_data:
            image_data = image_data.split(',', 1)[1]
        img_bytes = base64.b64decode(image_data)
    else:
        img_bytes = image_data

    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img


def _find_grid_params_from_lines(line_positions, board_size=19, img_size=None):
    """
    从检测到的线位置中，用 RANSAC 风格找到最佳的 (first, spacing) 参数,
    使得恰好有 19 个等间距位置都有对应的检测线。
    
    返回: (first_line_pos, spacing) 或 (None, None)
    """
    lines = np.sort(np.array(line_positions, dtype=np.float64))
    n = len(lines)
    if n < board_size:
        return None, None

    # 估算 spacing 范围
    if img_size:
        est_spacing = img_size / 22.0
    else:
        est_spacing = (lines[-1] - lines[0]) / 20.0

    min_spacing = est_spacing * 0.8
    max_spacing = est_spacing * 1.2

    best_error = float('inf')
    best_first = None
    best_spacing = None

    # 对每对近邻线，假设它们间隔 k 个格距
    for i in range(n):
        for j in range(i + 1, min(n, i + 5)):
            dist = lines[j] - lines[i]
            for k in [1, 2]:
                spacing = dist / k
                if spacing < min_spacing or spacing > max_spacing:
                    continue

                # 计算所有线相对于此 spacing 的相位
                phases = (lines - lines[i]) / spacing
                nearest_int = np.round(phases)
                residuals = np.abs(phases - nearest_int)

                # 内点: 残差 < 0.15
                inliers = residuals < 0.15
                n_inliers = np.sum(inliers)

                if n_inliers >= board_size:
                    inlier_positions = nearest_int[inliers].astype(int)
                    min_pos = inlier_positions.min()
                    shifted = inlier_positions - min_pos

                    # 检查 0-18 是否都被覆盖
                    covered = set(shifted.tolist())
                    if all(kk in covered for kk in range(board_size)):
                        first = lines[i] - nearest_int[i] * spacing + min_pos * spacing
                        total_error = np.sum(residuals[inliers])

                        if total_error < best_error:
                            best_error = total_error
                            best_spacing = spacing
                            best_first = first

    return best_first, best_spacing


def _calibrate_grid_from_lines(gray, board_size=19):
    """
    用 HoughLines 检测棋盘格线，RANSAC 式找出 19 条等间距线。
    
    返回: (first_x, first_y, cell_size) 或 None
    """
    h, w = gray.shape

    edges = cv2.Canny(gray, 30, 100, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=200)

    if lines is None:
        return None

    h_rhos = []
    v_rhos = []
    for line in lines:
        rho, theta = line[0]
        if abs(theta - np.pi / 2) < 0.1:
            h_rhos.append(rho)
        elif abs(theta) < 0.1 or abs(theta - np.pi) < 0.1:
            v_rhos.append(abs(rho))

    # 聚类相近的线
    def cluster(positions, gap=15):
        if not positions:
            return []
        positions = sorted(positions)
        clusters = [[positions[0]]]
        for p in positions[1:]:
            if p - clusters[-1][-1] < gap:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return [np.mean(c) for c in clusters]

    h_clusters = cluster(h_rhos)
    v_clusters = cluster(v_rhos)

    # 需要足够多的线
    if len(h_clusters) < board_size or len(v_clusters) < board_size:
        return None

    h_first, h_spacing = _find_grid_params_from_lines(h_clusters, board_size, h)
    v_first, v_spacing = _find_grid_params_from_lines(v_clusters, board_size, w)

    if h_first is not None and v_first is not None:
        cell_size = (h_spacing + v_spacing) / 2
        return v_first, h_first, cell_size

    return None


def _calibrate_grid_from_stones(img, board_size=19):
    """
    用霍夫圆检测找到棋子位置，从棋子坐标分布反推网格参数。
    
    原理: 所有棋子都在网格交叉点上，坐标 mod 格距 应该集中在一个值。
    通过 circular statistics 搜索最佳格距。
    
    返回: (first_x, first_y, cell_size) 或 None
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, w = gray.shape

    blurred = cv2.GaussianBlur(gray, (5, 5), 1.5)
    img_size = min(h, w)
    est_cell = img_size / 22.0
    min_r = max(8, int(est_cell * 0.28))
    max_r = int(est_cell * 0.55)
    min_dist = max(20, int(est_cell * 0.6))

    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1, minDist=min_dist,
        param1=60, param2=28,
        minRadius=min_r, maxRadius=max_r
    )

    if circles is None:
        return None

    # 过滤: 只保留确实是棋子的圆
    stone_positions = []
    for c in circles[0]:
        cx, cy, r = int(c[0]), int(c[1]), int(c[2])
        mask = np.zeros_like(gray)
        cv2.circle(mask, (cx, cy), int(r * 0.6), 255, -1)
        brightness = cv2.mean(gray, mask=mask)[0]
        sat = cv2.mean(hsv[:, :, 1], mask=mask)[0]
        if brightness < 120 or (brightness > 160 and sat < 60):
            stone_positions.append((cx, cy))

    if len(stone_positions) < 8:
        return None

    xs = np.array([p[0] for p in stone_positions], dtype=np.float64)
    ys = np.array([p[1] for p in stone_positions], dtype=np.float64)

    # 搜索最佳格距
    best_score = 0
    best_cell = est_cell
    for cell_try in np.arange(est_cell * 0.7, est_cell * 1.3, 0.5):
        x_angles = (xs % cell_try) / cell_try * 2 * np.pi
        y_angles = (ys % cell_try) / cell_try * 2 * np.pi
        x_r = np.sqrt(np.mean(np.cos(x_angles)) ** 2 + np.mean(np.sin(x_angles)) ** 2)
        y_r = np.sqrt(np.mean(np.cos(y_angles)) ** 2 + np.mean(np.sin(y_angles)) ** 2)
        score = x_r + y_r
        if score > best_score:
            best_score = score
            best_cell = cell_try

    cell_size = best_cell

    # 计算 offset (mod cell_size)
    def find_offset(coords, cell):
        best_err = float('inf')
        best_off = 0
        for off_try in np.arange(0, cell, 0.5):
            residuals = (coords - off_try) % cell
            dist = np.minimum(residuals, cell - residuals)
            err = np.mean(dist)
            if err < best_err:
                best_err = err
                best_off = off_try
        return best_off

    x_offset = find_offset(xs, cell_size)
    y_offset = find_offset(ys, cell_size)

    # 确定绝对 first 位置: 从 offset 出发，约束所有棋子在 [0, 18]
    board_span = (board_size - 1) * cell_size

    # X: first_x 使得所有棋子列号在 0-18
    first_x = x_offset
    while first_x > xs.min():
        first_x -= cell_size
    while first_x + board_span < xs.max():
        first_x -= cell_size
    # 微调: 让 first_x 尽量靠近图像左边缘(完整棋盘截图的假设)
    while first_x + cell_size <= xs.min() and first_x + cell_size > 0:
        test = first_x + cell_size
        if test + board_span >= xs.max():
            first_x = test
        else:
            break

    # Y: 同理
    first_y = y_offset
    while first_y > ys.min():
        first_y -= cell_size
    while first_y + board_span < ys.max():
        first_y -= cell_size
    while first_y + cell_size <= ys.min() and first_y + cell_size > 0:
        test = first_y + cell_size
        if test + board_span >= ys.max():
            first_y = test
        else:
            break

    # 验证
    valid_count = 0
    for cx, cy in stone_positions:
        col = round((cx - first_x) / cell_size)
        row = round((cy - first_y) / cell_size)
        if 0 <= col < board_size and 0 <= row < board_size:
            exp_x = first_x + col * cell_size
            exp_y = first_y + row * cell_size
            if abs(cx - exp_x) < cell_size * 0.35 and abs(cy - exp_y) < cell_size * 0.35:
                valid_count += 1

    if valid_count < len(stone_positions) * 0.7:
        return None

    return first_x, first_y, cell_size


def calibrate_grid(img, board_size=19):
    """
    标定棋盘网格。
    
    优先级:
    1. 格线法 (HoughLines + RANSAC): 最精确，能确定绝对位置
    2. 棋子法 (HoughCircles + circular statistics): 当格线不足时使用
    
    返回: (first_x, first_y, cell_size) 或 None
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Method 1: 格线法
    result = _calibrate_grid_from_lines(gray, board_size)
    if result is not None:
        return result

    # Method 2: 棋子法
    result = _calibrate_grid_from_stones(img, board_size)
    if result is not None:
        return result

    return None


def classify_intersections(img, intersections, cell_size, board_size=19):
    """
    对每个交叉点进行棋子分类。
    
    使用霍夫圆检测找到棋子，再按颜色分类映射到最近格点。
    这比逐格点采样更可靠，因为霍夫圆能精确定位棋子中心。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, w = gray.shape

    blurred = cv2.GaussianBlur(gray, (5, 5), 1.5)
    min_r = max(8, int(cell_size * 0.28))
    max_r = int(cell_size * 0.55)
    min_dist = max(20, int(cell_size * 0.6))

    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1, minDist=min_dist,
        param1=60, param2=28,
        minRadius=min_r, maxRadius=max_r
    )

    board = [[0] * board_size for _ in range(board_size)]
    stones = []

    if circles is not None:
        first_x = intersections[0, 0, 0]
        first_y = intersections[0, 0, 1]

        for c in circles[0]:
            cx, cy, r = int(c[0]), int(c[1]), int(c[2])

            # 映射到格点
            col_idx = round((cx - first_x) / cell_size)
            row_idx = round((cy - first_y) / cell_size)

            if not (0 <= col_idx < board_size and 0 <= row_idx < board_size):
                continue

            # 验证位置偏差不超过 0.4 格
            exp_x = first_x + col_idx * cell_size
            exp_y = first_y + row_idx * cell_size
            if abs(cx - exp_x) > cell_size * 0.4 or abs(cy - exp_y) > cell_size * 0.4:
                continue

            # 颜色判断: 用棋子中心区域
            mask = np.zeros_like(gray)
            cv2.circle(mask, (cx, cy), int(r * 0.6), 255, -1)
            brightness = cv2.mean(gray, mask=mask)[0]
            sat = cv2.mean(hsv[:, :, 1], mask=mask)[0]

            stone_type = 0
            if brightness < 130 and sat < 75:
                stone_type = 1  # 黑
            elif brightness > 140 and sat < 55:
                stone_type = 2  # 白

            if stone_type != 0:
                pos = COLS_LABEL[col_idx] + str(board_size - row_idx)
                # 避免重复
                if board[row_idx][col_idx] == 0:
                    board[row_idx][col_idx] = stone_type
                    stones.append({
                        "color": "B" if stone_type == 1 else "W",
                        "row": row_idx,
                        "col": col_idx,
                        "pos": pos
                    })

    # 补充: 逐格点采样检测 (捕获霍夫圆可能遗漏的棋子)
    r_sample = max(3, int(cell_size * 0.22))
    for row in range(board_size):
        for col in range(board_size):
            if board[row][col] != 0:
                continue  # 已有棋子

            cx = int(intersections[row, col, 0])
            cy = int(intersections[row, col, 1])

            if cx < r_sample or cx >= w - r_sample or cy < r_sample or cy >= h - r_sample:
                continue

            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.circle(mask, (cx, cy), r_sample, 255, -1)
            gray_val = cv2.mean(gray, mask=mask)[0]
            sat_val = cv2.mean(hsv[:, :, 1], mask=mask)[0]

            stone_type = 0
            if gray_val < 120 and sat_val < 70:
                stone_type = 1
            elif gray_val > 160 and sat_val < 45:
                stone_type = 2

            if stone_type != 0:
                board[row][col] = stone_type
                pos = COLS_LABEL[col] + str(board_size - row)
                stones.append({
                    "color": "B" if stone_type == 1 else "W",
                    "row": row,
                    "col": col,
                    "pos": pos
                })

    return board, stones


def recognize_board(image_data, board_size=19):
    """
    主函数：识别棋盘上的棋子位置。
    
    参数:
        image_data: base64 编码的图片数据
        board_size: 棋盘大小 (默认 19)
    
    返回:
        {
            "status": "ok",
            "stones": [{"color": "B"/"W", "row": r, "col": c, "pos": "D4"}, ...],
            "board": [[0/1/2, ...], ...],
            "stone_count": {"black": n, "white": m},
        }
    """
    img = decode_image(image_data)
    if img is None:
        return {"error": "无法解码图片"}

    # 1. 标定网格
    calibrated = calibrate_grid(img, board_size)
    if calibrated is None:
        return {"error": "无法识别棋盘网格"}

    first_x, first_y, cell_size = calibrated

    # 2. 生成交叉点坐标
    intersections = np.zeros((board_size, board_size, 2), dtype=np.float64)
    for row in range(board_size):
        for col in range(board_size):
            intersections[row, col, 0] = first_x + col * cell_size
            intersections[row, col, 1] = first_y + row * cell_size

    # 3. 分类棋子
    board, stones = classify_intersections(img, intersections, cell_size, board_size)

    return {
        "status": "ok",
        "stones": stones,
        "board": board,
        "board_size": board_size,
        "stone_count": {
            "black": sum(1 for s in stones if s["color"] == "B"),
            "white": sum(1 for s in stones if s["color"] == "W")
        },
        "grid_info": {
            "first_x": round(first_x, 1),
            "first_y": round(first_y, 1),
            "cell_size": round(cell_size, 2)
        }
    }


if __name__ == "__main__":
    """命令行测试"""
    import sys
    if len(sys.argv) < 2:
        print("用法: python board_recognition.py <图片路径>")
        sys.exit(1)

    img_path = sys.argv[1]
    with open(img_path, 'rb') as f:
        img_data = base64.b64encode(f.read()).decode()

    result = recognize_board(img_data)

    if "error" in result:
        print(f"错误: {result['error']}")
    else:
        print(f"识别结果: 黑子 {result['stone_count']['black']} 枚, 白子 {result['stone_count']['white']} 枚")
        if "grid_info" in result:
            gi = result["grid_info"]
            print(f"网格: first=({gi['first_x']}, {gi['first_y']}), cell={gi['cell_size']}")
        print("\n棋子位置:")
        for s in sorted(result['stones'], key=lambda x: x['pos']):
            print(f"  {s['color']} {s['pos']}")

        print("\n棋盘 (●=黑 ○=白 ·=空):")
        for r in range(len(result['board'])):
            row_str = ""
            for c in range(len(result['board'][r])):
                v = result['board'][r][c]
                row_str += "● " if v == 1 else ("○ " if v == 2 else "· ")
            print(f"  {19 - r:2d} {row_str}")
