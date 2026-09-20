# case enrollment 台账（派生视图）

真值是 [`enrollment.json`](enrollment.json)，本文件由 `tools/generate_enrollment.py` 生成，不手改。
状态含义见 [`03_CI_POLICY.md`](03_CI_POLICY.md) §3 与 ADR 0053 §五：**只有 enforced 且结果目录里有有效通过记录的实例才算通过**；
planned / observing / later 是登记，不是成绩。

能力版本：`u01` · 计数：enforced 1 · later 1 · planned 31

| case | 标题 | enrollment | lane | 阶段 | 用例 | fixture | 场景 |
|---|---|---|---|---|---|---|---|
| FO01 | 脚本同目录 CSV | planned | pr | U03 | — | — | FO01 |
| FO02 | 脚本目录与运行根目录不同 | planned | integration | U03 | — | — | FO02 |
| FO03 | __file__ 与模块相对导入 | planned | pr | U03 | — | — | FO03 |
| FO04 | 项目外有效绝对路径 | planned | integration | U03 | — | — | FO04 |
| FO05 | h5py 原生读取 | planned | integration | U03 | — | — | FO05 |
| FO06 | exists/glob/listdir/read 一致 | planned | integration | U03 | — | — | FO06 |
| FO07 | 同名干扰数据 | planned | pr | U03 | — | — | FO07 |
| FO08 | 项目移动与外部数据失联 | planned | integration | U03 | — | — | FO08 |
| FO09 | 中文空格、大小写、跨盘符 | planned | integration | U03 | — | — | FO09 |
| FO10 | 读写权限与受保护原件 | planned | integration | U03 | — | — | FO10 |
| FO11 | 真实不同 Python minor | planned | integration | U03 | — | — | FO11 |
| FO12 | 宿主 AST 不认识目标合法语法 | planned | integration | U03 | — | — | FO12 |
| FO13 | 真实二进制依赖 ABI 隔离 | planned | integration | U04 | — | — | FO13 |
| FO14 | 项目外命名 Conda 环境 | later | nightly | X01 | — | — | FO14 |
| FO15 | 显式环境与项目约束冲突 | planned | pr | U03 | — | — | FO15 |
| FO16 | 不支持的 Python/能力 | planned | integration | U03 | — | — | FO16 |
| FO17 | 同一基础 Python 的两个 venv | planned | integration | U03 | — | — | FO17 |
| FO18 | 三个以上额外依赖联合准备 | planned | integration | U04 | — | — | FO18 |
| FO19 | 本地实验室模块和重名引擎模块 | planned | pr | U03 | — | — | FO19 |
| FO20 | markers/extras/所选依赖组 | planned | integration | U04 | — | — | FO20 |
| FO21 | 依赖约束不可同时满足 | planned | integration | U04 | — | — | FO21 |
| FO22 | 已安装但原生库无法 import | planned | integration | U04 | — | — | FO22 |
| FO23 | 无系统 Python/uv/pip 冷启动 | planned | release | U05 | — | — | FO23 |
| FO24 | 离线且受管 runtime/wheels 缓存齐备 | planned | integration | U05 | — | — | FO24 |
| FO25 | 离线且无可用缓存 | planned | pr | U05 | — | — | FO25 |
| FO26 | 下载损坏、截断和错误哈希 | planned | integration | U05 | — | — | FO26 |
| FO27 | 准备/运行阶段取消 | planned | integration | U04 | — | — | FO27 |
| FO28 | 磁盘不足和只读目录 | planned | integration | U04 | — | — | FO28 |
| FO29 | 并发项目与活跃 native 会话 | planned | integration | U04 | — | — | FO29 |
| FO30 | 预检后输入或环境改变 | planned | integration | U09 | — | — | FO30 |
| FO31 | 首开/二开/会话重启不重复准备 | planned | pr | U04 | — | — | FO31 |
| FO32 | 真实打开—编辑—重放—导出 | planned | release | U09 | — | — | FO32 |
| U01-S1 | single_file_csv 经真实 HTTP 服务（会话认证）首开 → 准备 → 渲染 → 旧后端导出 PDF/PNG → 独立读回 | enforced | pr | U01 | `tests/test_foundation_harness.py::test_u01_s1_first_open_and_export_through_the_public_entry` | `tests/fixtures/foundation/single_file_csv` | FO01, FO32 |
