"""U00 fixture ② 的本地包：只有 `python scripts/entry.py` 那种把脚本目录放进
`sys.path[0]` 的启动方式才 import 得到。它不是 PyPI 上的包，也不该被当作缺失依赖
去安装——依赖解析里「未知本地模块」这一档说的就是它。"""

SLOPE = 2.0
INTERCEPT = 1.0


def predict(t: float) -> float:
    """v = 2·t + 1（与 `data/points.csv` 的真值同一条公式）。"""
    return SLOPE * t + INTERCEPT
