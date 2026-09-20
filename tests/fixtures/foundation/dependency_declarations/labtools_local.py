"""未知本地模块：只存在于这个目录里，PyPI 上没有同名包。

`script_unknown_local.py` 平铺 `import labtools_local`，`python script_unknown_local.py`
下因为 `sys.path[0]` 是脚本目录而 import 得到。依赖解析必须把它归入「本地模块 / 不是
包」那一档——绝不能因为 import 名叫这个就去索引上找同名的东西装（抢注入口）。
"""

FACTOR = 4


def scale(v: float) -> float:
    return FACTOR * v
