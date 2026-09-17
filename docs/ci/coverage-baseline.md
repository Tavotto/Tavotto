# 重点纯模块的分支覆盖基线（2026-09-17，main @ 77f23250）

审计任务书第四节第 5 条：**先测量再约束**。本仓库没有覆盖率工具（CI 里没有 coverage /
pytest-cov），这是第一次量。量的是 `[tool.mutmut] only_mutate` 圈住的那几个纯逻辑模块——
它们纯标准库、逻辑密集、算错了会安静地产生错误结果，也是变异测试的 scope；全仓百分比
不是目标（任务书原话）。

## 怎么量的

```sh
python -m venv /tmp/cov && /tmp/cov/bin/pip install coverage pytest flask pymupdf
COVERAGE_FILE=/tmp/covdata/f PYTHONPATH=src /tmp/cov/bin/python -m coverage run --branch \
  --source=src/tavotto/engine \
  --include='*/engine/patchspec.py,*/engine/registry.py,*/engine/locate.py,*/engine/preflight.py,*/engine/profiles.py' \
  -m pytest tests/test_patchspec.py tests/test_registry.py tests/test_install_locate.py \
     tests/test_preflight.py tests/test_profile_store.py tests/test_discover.py \
     tests/test_handoff.py tests/test_export_request.py tests/test_mcp_normalize.py -q
COVERAGE_FILE=/tmp/covdata/f /tmp/cov/bin/python -m coverage report --show-missing --include='…同上…'
```

**范围声明**：只跑了这些模块**点名的**用例文件（9 个），不是全套 4858 条。全套跑出来的数字
只会更高，不会更低；这里的数字是「这个模块自己的看护用例够不够」，不是「产品路径有没有
走到」。worker 子进程里跑的模块（manifest / overrides / figsession / axestraversal 那一族）
需要跨进程的 coverage 钩子，**本次没量**。

## 数字

| 模块 | 语句 | 未覆盖 | 分支 | 部分覆盖的分支 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `engine/patchspec.py` | 68 | 1 | 36 | 1 | 98% |
| `engine/registry.py` | 95 | 3 | 22 | 0 | 97% |
| `engine/locate.py` | 238 | 19 | 100 | 11 | 91% |
| `engine/profiles.py` | 132 | 15 | 60 | 9 | 88% |
| `engine/preflight.py` | 429 | 74 | 236 | 34 | 79% |
| 合计 | 962 | 112 | 454 | 55 | 86% |

2026-09-17 新提出来的 `engine/bakedbaseline.py`（PR E，用它自己的四个用例文件量）：94 语句 /
20 分支，**96%**——没盖到的两处都是 `OSError` 分支（迁移写盘失败退回读旧文件；同尺寸比
sha1 时读文件失败），PR E 里补。

## 缺口点名（哪些分支没有 pytest 侧的看护）

- **`preflight.py` 79%，最低的一个**，缺口集中在三段：
  * 666–683 `discouraged-colormap`（不推荐的色谱）；
  * 778–791 面板贴边 / 出血（`near` 那一段）；
  * 845–910 **画布标注文字**的字号下限、字形替换、CJK 要求——这一整段在 pytest 侧没跑到。
    两份求值器靠 `tests/golden/preflight_vectors.json` 对齐，TS 侧 `preflight.golden.test.ts`
    跑同一份向量；**要核实向量里有没有画布标注的样例**：如果没有，这一段两侧都只有单元测试
    在守。
- `locate.py` 91%：177–189 是 `winreg`（Windows 注册表）分支，只在 Windows lane 跑得到；
  428–430 / 507–508 是安装清单损坏的兜底。
- `profiles.py` 88%：102–117 是 `importlib.resources` 退回源码树那两条路（装成 wheel 之后才
  走另一条）。

## 增量门槛（建议，本 PR 不实施）

- 先把上面这张表当基线：这几个模块的**分支**覆盖不许比基线低（容差 1 个百分点，免得
  重构时挪一行就红）。门槛落在 backend-fast 之外的一条 report-only 步骤（与 mutmut 同一
  档），跑上一个月再决定要不要转成门禁。
- 新提出来的纯模块（bakedbaseline / axestraversal 那一类）进 `only_mutate` 的同时进这张表。
- 全仓 90% / 100% 不作目标；app.py 这种集成层不量分支覆盖（它的看护在 e2e 与冒烟）。
