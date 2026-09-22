# U00 合成 fixture（统一实施包 · 基线）

八组**全合成、小体积、进 git** 的项目夹具（前六组 U00 建，第七组 U03 加，第八组 U04 加），服务 `docs/implementation/tavotto-foundation/`
的 U01–U09（FirstOpenBench / RenderBench / 联合依赖）。每组自带 `truth.json`：输入真值
**不用产品代码就能校验**（`tests/test_foundation_fixtures.py`）。这些夹具在 U00 只被
「真值测试」与「原生参考隔离测试」消费；**没有一条产品用例挂在它们上**（首开链路的
产品资格是 U03 起的事，U00 一律 `not_run`）。

| 目录 | 场景 | 真值 | 原生参考跑法 |
|---|---|---|---|
| `single_file_csv/` | ① 单文件 matplotlib + 同目录 CSV，相对路径读 | y = 1.5x + 1 → [2.5, 4.0, 5.5, 7.0] | 复制整目录到临时目录，`cd` 进去 `python figure.py` → `figure.pdf` |
| `split_scripts_data/` | ② `scripts/entry.py` + `data/` 分离；`--data-via file`（按 `__file__`，任何 cwd 都对）/ `--data-via cwd`（只有 cwd = 项目根才对）；本地包 `scripts/labhelpers` | v = 2t + 1 → [1, 3, 5, 7] | 项目根 `python scripts/entry.py [--data-via cwd]` → `entry.pdf`；cwd = `scripts/` 时 `--data-via cwd` 必须 FileNotFoundError |
| `same_name_data/` | ③ 同名不同值：`data.csv` [2, 4, 8] 正确、`decoy/data.csv` [200, 400, 800] 干扰；脚本画 3x + 1 | 正确 [7, 13, 25]；干扰 [601, 1201, 2401] | 本目录 `python plot.py --dump` 打 `7,13,25`；`cd decoy && python ../plot.py --dump` 打 `601,1201,2401`（两边都不报错——只有数值能分） |
| `project_venv/` | ④ 项目自带 `.venv`，基础解释器**与应用的不同**；`make_venv.py` 现建（不进 git、绝不 pip install） | n = 10k → [10, 20, 30]；解释器身份在回执与 stdout | `python make_venv.py --python <另一个 python> --app-python <应用的> [--link-host-site]`，再 `.venv/bin/python figure.py` |
| `dependency_declarations/` | ⑤ 小型非内置依赖（six / tabulate / sortedcontainers）+ marker 为假 / extra / 约束冲突 / 未知本地模块 / 不存在的导入名 | 每条声明的期望处置在 `truth.json` | 不跑（声明样例；安装由 U04 用本地 wheel 离线做） |
| `pdf_png_assets/` | ⑥ 一页 PDF（文字 + 图形 + α=0.5 透明 + 非对称页盒 CropBox [15 10 285 170] / MediaBox 300×200）+ 带 `pHYs` 300 dpi 与 `tEXt` 的 RGBA PNG + 一张没有 `pHYs` 的同图 | `truth.json`（页盒、内缩、文字、字体、alpha、PNG 尺寸 / 密度 / 文本块） | `python make_assets.py` 重生成，字节确定 |
| `joint_dependencies/` | ⑧（U04）脚本开跑就要三个只存在于测试 wheelhouse 里的纯 Python 包（用例现造：`support.dependency_repair.build_wheel`）；`requirements.txt` 带 extra / 上界 / marker 为假，`requirements-train.txt` 是未选的组 | y = 42·x → [42, 84, 126]；装的 / 不装的各一张表 | 先把三个 wheel 装进一个 venv 再本目录 `python figure.py` → `figure.pdf` |
| `private_python/` | ⑨（U05）干净机器：脚本要一个只存在于测试 wheelhouse 里的纯 Python 包，而这台机器没有任何可用的 Python（用例把发现链末端置空）——Tavotto 先按锁自备私有 Python，再建受管环境装它 | y = 42·x → [42, 84, 126]；标题 `u05 private python` | 把 wheel 装进一个 venv 再本目录 `python figure.py` → `figure.pdf` |
| `shadowed_engine_modules/` | ⑦（U03）用户自己的 `manifest.py` / `overrides.py`（与引擎模块重名）+ 本地包 `lab_utils/`；脚本 `import manifest` 必须命中用户那份（issue #447 / FO19） | 标题 sentinel `user-manifest|user-overrides|user-lab_utils`；y = [3, 6, 12] | 本目录 `python figure.py` → `figure.pdf`，标题里三个 sentinel 齐全 |

## 纪律

* **原生参考在独立目录跑**：测试先把夹具目录复制到临时目录再执行，并对夹具目录做
  执行前后的文件快照比对——原生跑法**不许**往 `tests/fixtures/foundation/` 写任何东西
  （产物、`__pycache__`、matplotlib 缓存都不许）。`MPLCONFIGDIR` 指到临时目录。
* **harness 不替产品装包**：`make_venv.py` 只 `python -m venv`，`--link-host-site` 只是让
  基础解释器上已有的 site-packages 可见（模拟「用户本来就装了」），一个字节都不下载。
  待产品安装的包（⑤ 里那几条）由 U04 的用例在离线 wheel 下交给产品去装。
* **真值是文件说的，不是产品说的**：`truth.json` 由夹具作者写死，测试用标准库解析
  CSV / PDF / PNG 与之对照；`pdf_png_assets/truth.json` 由 `make_assets.py` 生成，
  测试另外用正则 / `struct` 独立读一遍文件核对（两侧不同源）。
* 首开失败（例如 ② 的 `cwd` 模式在 Tavotto 沙盒下读不到数据）在 U00 只记录为
  `not_run` / `product_failure`，**不挂成 required**。
