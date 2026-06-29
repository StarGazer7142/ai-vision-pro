def point_in_polygon(x: float, y: float, polygon: list) -> bool:
    """Ray-casting point-in-polygon for normalized coordinates (0-1)."""
    num = len(polygon)
    if num < 3:
        return False
    inside = False
    j = num - 1
    for i in range(num):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside

# 测试围栏内侧滞留区（全屏）
polygon = [
    [0.0, 0.0],
    [1.0, 0.0],
    [1.0, 1.0],
    [0.0, 1.0]
]

# 测试点
points = [
    (0.15, 0.15),  # 中心点，应该在区域内
    (0.5, 0.5),    # 中心点，应该在区域内
    (0.9, 0.9),    # 右下角，应该在区域内
    (0.0, 0.0),    # 左下角，应该在区域内
    (1.0, 1.0),    # 右上角，应该在区域内
    (-0.1, 0.5),   # 左侧外，应该不在区域内
    (1.1, 0.5),    # 右侧外，应该不在区域内
    (0.5, -0.1),   # 下侧外，应该不在区域内
    (0.5, 1.1)     # 上侧外，应该不在区域内
]

print("测试 point_in_polygon 函数:")
for x, y in points:
    result = point_in_polygon(x, y, polygon)
    print(f"点 ({x}, {y}) 在区域内: {result}")