# FirstOpenBench 场景准入建议（生成物）

这是未来测试分层建议，不是已安装的runner配置。所有场景当前planned/later、not_run。
旧minimum_deep_lane保存在registry原字段，本表以统一阶段和03政策重排。

| ID | 场景 | 实现阶段 | 建议深度lane | 预期产品结果（原合同） | 准入 | 产品结果 |
|---|---|---|---|---|---|---|
| FO01 | 脚本同目录 CSV | U03 | pr_candidate | automatic | enforced | not_run |
| FO02 | 脚本目录与运行根目录不同 | U03 | integration_candidate | guided | enforced | not_run |
| FO03 | __file__ 与模块相对导入 | U03 | pr_candidate | automatic | enforced | not_run |
| FO04 | 项目外有效绝对路径 | U03 | integration_candidate | automatic | planned | not_run |
| FO05 | h5py 原生读取 | U03 | integration_candidate | automatic | planned | not_run |
| FO06 | exists/glob/listdir/read 一致 | U03 | integration_candidate | guided | planned | not_run |
| FO07 | 同名干扰数据 | U03 | pr_candidate | guided | enforced | not_run |
| FO08 | 项目移动与外部数据失联 | U03 | integration_candidate | guided | planned | not_run |
| FO09 | 中文空格、大小写、跨盘符 | U03 | integration_candidate | automatic | planned | not_run |
| FO10 | 读写权限与受保护原件 | U03 | integration_candidate | contractual | planned | not_run |
| FO11 | 真实不同 Python minor | U03 | integration_candidate | automatic | observing | not_run |
| FO12 | 宿主 AST 不认识目标合法语法 | U03 | integration_candidate | automatic | observing | not_run |
| FO13 | 真实二进制依赖 ABI 隔离 | U04 | integration_candidate | automatic | planned | not_run |
| FO14 | 项目外命名 Conda 环境 | X01 | nightly_extension | guided | later | not_run |
| FO15 | 显式环境与项目约束冲突 | U03 | pr_candidate | safe_stop | enforced | not_run |
| FO16 | 不支持的 Python/能力 | U03 | integration_candidate | safe_stop | observing | not_run |
| FO17 | 同一基础 Python 的两个 venv | U03 | integration_candidate | automatic | observing | not_run |
| FO18 | 三个以上额外依赖联合准备 | U04 | integration_candidate | guided | planned | not_run |
| FO19 | 本地实验室模块和重名引擎模块 | U03 | pr_candidate | automatic | enforced | not_run |
| FO20 | markers/extras/所选依赖组 | U04 | integration_candidate | guided | planned | not_run |
| FO21 | 依赖约束不可同时满足 | U04 | integration_candidate | safe_stop | planned | not_run |
| FO22 | 已安装但原生库无法 import | U04 | integration_candidate | safe_stop | planned | not_run |
| FO23 | 无系统 Python/uv/pip 冷启动 | U05 | release | guided | observing | not_run |
| FO24 | 离线且受管 runtime/wheels 缓存齐备 | U05 | integration_candidate | automatic | planned | not_run |
| FO25 | 离线且无可用缓存 | U05 | pr_candidate | safe_stop | planned | not_run |
| FO26 | 下载损坏、截断和错误哈希 | U05 | integration_candidate | safe_stop | planned | not_run |
| FO27 | 准备/运行阶段取消 | U04 | integration_candidate | safe_stop | planned | not_run |
| FO28 | 磁盘不足和只读目录 | U04 | integration_candidate | safe_stop | planned | not_run |
| FO29 | 并发项目与活跃 native 会话 | U04 | integration_candidate | contractual | planned | not_run |
| FO30 | 预检后输入或环境改变 | U09 | integration_candidate | contractual | planned | not_run |
| FO31 | 首开/二开/会话重启不重复准备 | U04 | pr_candidate | automatic | planned | not_run |
| FO32 | 真实打开—编辑—重放—导出 | U09 | release | guided | planned | not_run |

contractual案例必须在执行前拆成具体成功/停止合同；不能运行后选择更容易过的解释。
严禁用safe_stop测试pass计入自动兼容成功。FO23/FO32保留最终真实安装资格；FO14的新增命名Conda适配后置X01。
