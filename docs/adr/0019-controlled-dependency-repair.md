# ADR 0019：受控依赖修复（一键安装缺失的 Python 包）

状态：已实施（2026-08-27）
相关：[0018 项目 Python 环境解析](0018-project-python-environment-resolution.md)（本
ADR 建在它上面，**不改它一个字节**）、
[0014 Safe/Native Execution Profiles](0014-safe-native-execution-profiles.md)（仍是
Proposed；本轮**不实现** `tavotto run`）、
[兼容层分层](../compatibility/legacy-projects.md)、
[Compatibility Bridge 总纲](../compatibility/COMPATIBILITY_BRIDGE_MASTER_PLAN.md)。

## 背景

ADR 0018 让「项目自己带着一个能跑通的 `.venv`」这一类失败自动恢复了。剩下的
两类还是死路：

* 项目有 `.venv`，但**它也没有**那个包（`project_env_module_missing`）；
* 项目根本没有 venv（`project_env_not_found`）。

这两种局面下 Tavotto 当时给出的只有「选择其他 Python」。对一个只想改图注的
科研用户来说，那句话等价于「你先去学一下 pip、site-packages 和虚拟环境」。

产品目标只有一句：

```text
用户看到「缺少 lmfit」→ 点一次「安装并继续」→ 图出来
```

而不是：

```text
用户看到 ModuleNotFoundError → 去搜 pip 是什么
```

## 决策

在既有环境解析链上加一条**恢复能力**（不是新的解析链）：确定某个环境无法
运行用户脚本、且根因明确是 `missing_dependency` 时，提供「把这个包装进一个
**明确的**环境并继续」。

```text
missing_dependency（唯一触发器）
    ↓
解析：import 名 → 可信的 distribution         ← 解析不到就停在这儿
    ↓
选目标：项目 .venv（改用户环境）/ Tavotto 受管环境（改我们自己的）
    ↓
用户明确点击（改用户环境时文案说清「这会修改你的环境」）
    ↓
pip install（wheels 优先、shell=False、不 --upgrade）
    ↓
验证三层：import 那个包 / import matplotlib / **真起一次 worker 跑通 build**
    ↓
作废旧 worker → 用新环境重跑脚本 → Figure 出来
```

实现分四个模块，每个都是自己那件事的唯一出处：

| 模块 | 负责 |
| --- | --- |
| `engine/depresolve.py` | import 名 → distribution 的**可信**解析；包名语法 |
| `engine/managedenv.py` | Tavotto 替项目管的隔离环境（位置、manifest、创建） |
| `engine/deprepair.py` | 计划、安装、取消、验证、记账 |
| `engine/pool.py`（增量） | 环境改动期间的 worker 生命周期（谁都不许在这时起会话） |

## 一、三种环境，三种权限

| 环境 | 能不能装 | 为什么 |
| --- | --- | --- |
| **Tavotto 内置 runtime** | **永远不能** | 它是「重装就能修」这条退路的前提。被 pip resolver 逐渐污染之后，用户之间不再有同一个基线，我们的视觉基线、写回像素门、CompatBench 全部失去意义。缺包时它只是**触发器** |
| **项目 `.venv`** | 用户明确点击之后可以 | 那是他做研究的环境，兼容性最强（numpy/scipy/私有包都在里面）。但改它是**不可逆**的（见 §八） |
| **Tavotto 受管环境** | 可以 | 那是我们自己的东西，坏了可以整个删掉重建 |

第一条由 `tests/test_dependency_repair.py::test_the_bundled_runtime_is_never_a_mutation_target`
结构性看护：任何依赖修复路径上都不允许出现「内置解释器 + pip install」。

## 二、import 名不是包名

`ModuleNotFoundError: No module named 'PIL'` 里的 `PIL` 拿去 `pip install`
装到的是**另一个包**。这不是理论问题，是一条真实的供应链攻击路径（抢注
常见 import 名）。所以解析必须有可信来源，**只有三档**：

```text
project_declared   项目自己的 requirements.txt / pyproject.toml 里声明过
                   → 用项目声明的包名与版本约束
curated            Tavotto 维护的一张小而高质量的科研包映射
user_specified     用户自己输入的包名（仍要过严格语法校验）
```

**没有第四档，尤其没有「import 名同名试试看」。** 解析不到就是解析不到：
界面给「指定安装包…」和「选择其他 Python」，不给「安装并继续」。

curated 表刻意**小**。维护几千项的 PyPI 镜像意味着：会过期、要联网校准、
没人逐条审得动，而它换来的是「装错包」这一类最难发现的错误。同名包也要
**显式登记**（`SAME_NAME`）——「名字一样」本身不是证据。

`import PIL` + `pyproject: Pillow>=10` 这种情况要两档合起来才成立：curated
告诉我们 PIL 就是 Pillow，项目声明告诉我们版本要 `>=10`。

依赖声明**只读**：不改 requirements.txt、不改 pyproject.toml、不
`pip install -r`。解析失败（malformed pyproject、编码坏了）只意味着「这一档
解析源不可用」，绝不阻断——本来能靠 curated 修好的脚本不该被一份坏元数据连坐。

## 三、包名语法是安全边界，不是输入校验

即使 `shell=False`、argv 是 list，**pip 自己仍会把参数解析成选项**：

```text
-r evil.txt          → 读一个需求文件
--index-url http://… → 换一个包索引
--target /somewhere  → 装到别处
pkg @ https://…      → 直接下载一个 URL
```

所以用户能影响到的那个字符串必须先过一道**白名单语法**（`parse_requirement`），
第一版允许的**全部**形态是：

```text
package-name
package-name==1.2.3
package-name>=1.2
package-name>=1.2,<2
```

带空格的、带 `@` `/` `\` `;` `[` 的、URL、VCS、本地路径一律拒绝。
「未来可以做的高级功能」不等于「第一版先放行」——放行了就再也收不回来。

## 四、计划绑定，不是「确认了没有」

后端**不接受** `confirmed=true` 这种布尔量：任何一个能构造请求的页面都能带上它。

两步：

```text
POST /api/engine/dependency/plan     → 形成计划，发一个不可猜的 plan_id
POST /api/engine/dependency/install  → 请求体里**只有** plan_id
```

计划里绑死：

```text
plan_id / project / script / target_kind / python / env_fingerprint
requirement（包名 + 版本约束 + 解析来源）/ expires_at
```

执行端**只认计划**。装什么、装到哪、哪个项目，一律来自计划本身，请求体里的
任何别的字段都不读——这正是防 TOCTOU 的机制面：用户看到的是「把 lmfit 装进
项目 `.venv`」，点下去执行的必须是**那一件事**。

`env_fingerprint`（解释器路径 + `pyvenv.cfg` 的 mtime/size）在执行前重算一次。
确认期间环境被删掉重建、换成另一个 Python，一律 `repair_plan_stale`，让用户
重新看一遍再决定。

## 五、pip 的用法

```python
[python, "-m", "pip", "install",
 "--disable-pip-version-check", "--no-input",
 "--only-binary=:all:", requirement]
```

逐条：

* **`<python> -m pip`**，不是 PATH 上的 `pip`——那个 pip 属于哪个解释器全看
  PATH，而我们要装进的是一个**指定的**环境；
* **`--only-binary=:all:`**：一键路径只装 wheel。sdist 会调本机编译器、跑
  build backend、十几分钟起步，失败原因完全在 Tavotto 的控制面之外。没有
  wheel 时如实报 `dependency_requires_build`，让用户去终端里自己装；
* **没有 `--upgrade`**：默认就是 pip 的 only-if-needed。往用户的科研环境里
  装一个包，不该顺手把整个 NumPy/SciPy 栈升级掉——那是「昨天的图今天画不出来」
  的经典成因；
* `shell=False`，argv 是 list（全仓库纪律）。

**index 用 pip 自己的配置**：用户的 `PIP_INDEX_URL` / `pip.conf` 是那个环境
原有的行为，我们不覆盖也不绕过。但诊断里**只记 `custom_package_index: true/false`，
绝不记地址**——那条 URL 可能带凭据，也会泄漏用户所在机构。

## 六、pip 退出码 0 不等于修好了

验证有三层，缺一层都不算成功：

1. `import <缺的那个模块>` 在目标解释器里成功；
2. `import matplotlib` 成功；
3. **真起一次 Tavotto worker**（临时目录里一份最小脚本，走
   `execspec.worker_argv` 的同一条命令行，跑通一次 `build`）。

第三层不是形式主义。「import 得到」与「跑得起来」是两件事：字体缓存不可写、
某个 `.so` 只在子进程里崩、后端起不来——只有真跑一次才看得见。

pip exit 0 + import 失败是**常见**组合：装进了另一个环境、装的是同名的另一个
包、扩展模块的 ABI 与这个 Python 对不上，三种都是 exit 0。

## 七、装完必须重建 worker

磁盘上多一个包，**不会**让一个已经起来的解释器看见它：`sys.modules` 是缓存的、
已加载的动态库不会重载、import 系统的 finder 缓存也不会自己失效。

所以安装期间与安装之后各有一条纪律（`pool` 侧）：

* **安装期间**：这个环境上的 worker 全部关掉，且 `pool.get()` 拒绝起新会话
  （`environment_mutating`）。让一个 worker 在 site-packages 正在被写的时候
  继续 import，得到的是「有时成功、有时缺一个子模块」这种最难查的失败。
* **安装之后**：`pool.invalidate(script, project)` 点名作废，下一次请求用
  新解释器从头起。

锁的粒度是**一个环境**，不是全局：A 项目在装 lmfit，B 项目的健康 worker 照常
工作。同一个环境上不允许两个安装并发。

## 八、用户环境上的安装是**只进不退**的

取消一次安装之后：

* **受管环境**：标成 `incomplete`，下次不直接复用——我们自己的东西，重建即可。
* **用户的 `.venv`**：**不假装能完整 rollback**。pip 可能已经写了一部分文件、
  甚至已经改了某个传递依赖的版本；`pip uninstall` 恢复不了那个状态，硬做只会
  把「装了一半」变成「拆坏了」。取消后跑一次体检，并**如实告诉用户**
  「安装被取消，项目环境可能已发生部分修改」。

本轮因此**禁止任何自动 `pip uninstall`**。这也正是「改用户环境必须明确确认」
的理由——一个不可逆的动作不能藏在一个「确定」按钮后面。

## 九、受管环境是项目作用域的

```text
<data_dir>/environments/<项目指纹>/
    venv/
    environment.json
```

* **每个项目一个**。一个全局的共享环境会慢慢变成所有科研项目的依赖垃圾桶：
  A 项目要 numpy 1.x、B 项目要 2.x，后装的把先装的顶掉。
  `bootstrap.py` 里那个全局 `worker-env/` 是**另一件事**（「这台机器上一个
  科学栈都没有」的兜底），两者刻意不合并。
* **绝不建在用户项目里**（`<项目>/.tavotto-venv/` 会进他的 git、会被同步、
  会在他删项目时消失又悄悄重建）。运行时可写数据一律走 `config.data_dir()`
  ——仓库级不变量。
* **不用 `--system-site-packages`**：那会让「隔离环境」四个字不成立，基础
  解释器上的一次升级会当场改变这个项目的渲染结果。

新环境只装 **matplotlib**（numpy 由它带进来）。worker 侧真正 import 的只有
matplotlib / numpy / 标准库——`worker.py`、`figcapture.py`、`manifest.py`、
`overrides.py`、`pathgeom.py` 逐个查过。pandas / scipy / seaborn **不装**：
脚本真要用时会走同一条 missing_dependency 修复路，而预装它们等于替用户
下载几百 MB 他可能用不到的东西。

`environment.json` 如实记下我们装过什么（包名、请求的约束、**装到的版本**），
重建时照着装回去。但**不声称 lockfile 级复现**：某个版本从 index 上撤了就
如实报错，不悄悄换一个别的版本装上——「重建完跟以前不一样」比「重建失败」
难查得多。

## 十、不做静态扫描后批量安装

打开项目时 AST 扫出 14 个 import 就装 14 个包，是错的：其中有标准库、有
optional 分支、有平台相关、有本地模块。**真实的 `ModuleNotFoundError` 是执行
权威**，安装触发器只有它（或用户明确点选的项目已声明依赖）。

修复轮次上限 `MAX_DEPENDENCY_REPAIR_ROUNDS = 3`，而且**三轮都不是自动的**：
每一轮都要用户再点一次。上限挡的是「装完还缺、再装还缺」把用户拖进无尽循环。

## 十一、打开项目不联网

安装是唯一联网的动作，且必须由用户点击触发。没网 → `dependency_network_unavailable`
（可恢复错误，不无限重试）；有超时、可取消；界面说明「需要联网下载」。

**打开项目、渲染、报错这几步一个字节都不往外发。**

## 十二、隐私

* 安装日志经**两道**脱敏：pip 特有的那条（`Looking in indexes:` / `--index-url`
  / URL 里的凭据）在 `deprepair`，路径与密钥那条走诊断包同一份规则
  （`diagnostics.redact_text`）——不让「抹掉主目录名」这种规则有两份实现。
* 诊断包里的 `dependency_repair` 段只有：修过几轮、受管环境的状态与
  **包名+版本**。没有绝对路径、没有 pip 配置、没有 index 地址、没有环境变量。
* **本轮不加遥测事件。** `EVENTS` 表扩容意味着采集范围变化，按既有纪律要升
  `CONSENT_VERSION` 并让所有人重新同意一次——为了几个计数让全体用户重新表态，
  这笔账在本轮不划算。需要数据时单独一轮做，连同代理侧那份对拍表一起。

## 十三、供应链风险

这项功能的本质是**从 package index 下载并执行 Python 代码**。所以：

* 一键安装只允许 `project_declared` / `curated` 两档高置信解析；
* `user_specified` 要用户自己输入包名，且过同一道语法关；
* 未知 import **绝不**因为「同名」被安装；
* 包名与版本经 argv 传参，不进任何拼接的代码字符串。

## 十四、Session 7 的安全模型一个字节没变

修好依赖之后，脚本仍然跑在 **safe 档**：worker 沙盒 cwd、写入与删除守卫、
相对路径只读回退、savefig 捕获语义、写回事务全部照旧。

「用项目 `.venv` + pip install」**不是** native 执行的后门。
`tests/test_dependency_repair.py::test_repair_does_not_weaken_the_sandbox` 看护。

## 后果

* 「缺依赖」这一类失败第一次有了产品化的出路，且**每一步都能说清楚改了什么**。
* 多了一个会往磁盘写东西的子系统。它的边界靠三样东西守住：解析可信度、
  计划绑定、环境改动期间的 worker 纪律——每一条都有负向反证。
* 用户环境上的安装不可逆。这是自觉接受的代价（替代方案是假装能 rollback，
  那更糟），代价靠「明确确认 + 如实告知」平衡。
* 没有 wheel 的包、没有基础 Python 的机器、私有 index 上的包仍然走「选择
  其他 Python」——那条路一直在，没有被这一轮取代。
