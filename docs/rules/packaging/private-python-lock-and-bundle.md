# 私有完整 Python：锁与随包归档

> 原文出自 `packaging/AGENTS.md`「内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> `packaging/AGENTS.md`「必守要点」压缩前原文（2026-10-24，与正文同等有效）：两个 macOS 目标的 CPython 块与 `runtime-lock.json` 逐字相同、升级一起改；它不是渲染 runtime；归档不是解释器，运行时 sha256 等于锁才用；wheel/sdist 绝不带

<!-- agents-route
sheet: packaging/AGENTS.md
order: 40
改到: `src/tavotto/resources/private_python_lock.json`、`scripts/stage_private_python.py`、`build/private-python-bundle/`、`TAVOTTO_REQUIRE_PRIVATE_PYTHON`
主题: 私有 Python 的锁（ADR 0063）与随桌面安装包的归档（ADR 0111） → `private-python-lock-and-bundle.md`
必守要点: 两个 macOS 目标的 CPython 块与 `runtime-lock.json` 逐字相同、升级一起改；它不是渲染 runtime；wheel/sdist 绝不带
-->

- **私有完整 Python 的锁是另一份、但同源**（U05，ADR 0063）：`src/tavotto/resources/private_python_lock.json`
  钉的是受管环境**基础解释器**的来源（pbs install_only，五个目标），放在包内 `resources/` 是因为产品
  运行时要读它（`engine/privatepython.py` 的唯一输入；冻结产物经 `tavotto.spec` 的 `resources/` datas）。
  两个 macOS 目标的 CPython 块**必须**与本锁文件的 `macos-*` 逐字段相同（`tests/test_private_python.py`
  钉着，同源对表登记）——升级内置 runtime 的 CPython 时两份一起改。它**不是**渲染 runtime：内置那份
  仍不可污染，私有 Python 只当 `python -m venv` 的 base，落在用户数据目录 `<data_dir>/private-python/`。
  Windows 那边分工明确：embeddable 继续当渲染 runtime，pbs 当 base（embeddable 没有 venv / ensurepip）。
  每个目标的 `enabled` 在无系统 Python 的目标资格取得前保持 false。
- **私有 Python 归档随桌面安装包附带**（2026-09-29，ADR 0111）：`scripts/stage_private_python.py` 按**产品读的同一份锁**
  取本目标的 pbs 归档（经 `build_worker_runtime.download`，同一个缓存 `build/runtime-cache/`：macOS 两目标与内置 runtime
  同一份、缓存命中；Windows 另下 ~47 MB）放进 `build/private-python-bundle/`（只许一份）；`build_desktop.py` 在 PyInstaller
  之前调它（`--skip-private-python` 跳过并删掉上次备好的那份）；`tavotto.spec` 用同一把尺 `check_bundle()` 收进 `_internal/private-python/`
  （目的地与 `runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME` 同源），`TAVOTTO_REQUIRE_PRIVATE_PYTHON=1`（`desktop-tauri.yml`
  打开）时缺了拒绝打包。增量 macOS +25 MB、Windows +47 MB。它是**归档不是解释器**：运行时 sha256 等于锁才用、解到
  用户数据目录。wheel / sdist 绝不带（pyproject `exclude` 挡 `build/private-python-bundle/**`）。看护
  `tests/test_private_python_bundle.py`。
