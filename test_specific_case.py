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
    (0.0, 0.0),
    (1.0, 0.0),
    (1.0, 1.0),
    (0.0, 1.0)
]

# 测试点，与 test_signal.py 中发送的检测数据一致
x, y = 0.15, 0.15

print(f"测试点 ({x}, {y}) 是否在区域内:")
result = point_in_polygon(x, y, polygon)
print(f"结果: {result}")

# 详细打印计算过程
print("\n详细计算过程:")
num = len(polygon)
inside = False
j = num - 1
for i in range(num):
    xi, yi = polygon[i]
    xj, yj = polygon[j]
    print(f"边 ({xi}, {yi}) -> ({xj}, {yj})")
    yi_gt_y = yi > y
    yj_gt_y = yj > y
    print(f"  yi > y: {yi_gt_y}, yj > y: {yj_gt_y}")
    print(f"  (yi > y) != (yj > y): {yi_gt_y != yj_gt_y}")
    if yi_gt_y != yj_gt_y:
        numerator = (xj - xi) * (y - yi)
        denominator = (yj - yi) + 1e-9
        value = numerator / denominator + xi
        print(f"  (xj - xi) * (y - yi) / ((yj - yi) + 1e-9) + xi = {value}")
        print(f"  x < value: {x < value}")
        intersect = x < value
        if intersect:
            inside = not inside
            print(f"  相交，inside 变为: {inside}")
    j = i
print(f"\n最终结果: {inside}")