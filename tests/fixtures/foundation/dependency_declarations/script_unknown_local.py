"""U00 fixture ⑤ 的脚本：一个本地模块 + 一个真依赖 + 一个不存在的导入名。

* `labtools_local` —— 同目录的本地模块（不是包，不该被安装）；
* `tabulate`       —— `requirements.txt` 声明的真依赖；
* `zzz_not_a_real_distribution_u00` —— 既不在本地、也不在 PyPI（U00 造的名字），
  解析器对它只能报「未知」，不能猜。

用 `--dump` 只做 import 与数值，不画图，供解析器对照。真值：scale(2.5) = 10。
"""

import sys

import labtools_local

if "--dump" in sys.argv[1:]:
    print(labtools_local.scale(2.5))
    sys.exit(0)

import tabulate  # noqa: E402 —— 放在 --dump 之后：只验本地模块时不需要它
import zzz_not_a_real_distribution_u00  # noqa: E402, F401 —— 刻意不存在

print(tabulate.tabulate([[labtools_local.scale(2.5)]]))
