"""U03 合成 fixture ⑦ 的本地实验室包：不在 PyPI 上，不该被当成缺失依赖去装（FO19）。"""

WHO = "user-lab_utils"


def scaled(values, factor):
    return [v * factor for v in values]
