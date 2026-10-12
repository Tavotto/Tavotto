# ADR 0123：观察到的提供者只披露、不授权；互斥 provider 的切换按作用域分流；成功回执记「真正用的」provider

日期：2026-10-12 · 状态：**Proposed**（兼容性改造 P03，随该 PR 评审；叠在 P02 / ADR 0122 之上）
相关：[0119 Import 来源解析](0119-import-origin-static-evidence.md)、[0122 声明依赖是安装根](0122-declared-dependencies-are-install-roots.md)、
[0115 授权绑定实际影响](0115-dependency-authorization-binds-impact.md)；规则全文 `docs/rules/backend/dependency-repair-and-packages.md`「Import 来源进安装决策（P03）」。

## 问题

Import Origin（ADR 0119）已经能静态观察「这个 import 在环境里是谁提供的」——`cv2` 的四个 opencv 发行包、editable、本地 wheel、VCS、不完整 `.pth`。P02 让声明点名的互斥 provider 在联合计划里胜出。
审计（DEP-05 / 07 / 08 / 14）剩下三处没接上：

1. **看不见的替换**：运行环境里是 `opencv-python-headless`（或 `my-yaml-fork` 这种 editable），要建新一代时计划装的是表里的 `opencv-python` / `pyyaml`。这是 ADR 0119 §五定下的正确行为（观察不造安装名），
   但计划、影响、回执**都没有说出**「环境里现在是谁、新一代会换成谁」，用户与排障者只能事后去比 pip list。
2. **跨作用域的静默丢弃**：受管环境是项目级的，账本里每一笔记着它为哪个作用域（脚本所在目录）装的。`a/` 装了 `opencv-python`，`b/` 声明 headless——`generation_requirements` 合并时账上互斥的那条让位（P02 的设计，
   同一作用域里用户改声明就是要换），于是 `b/` 的计划直接 `ready`，装完 `a/` 的脚本悄悄换了 provider。D04 的作用域互斥只比较**版本**，不认互斥 provider。
3. **回执只有版本**：`result.installed` 是 `{发行包: 版本}`，追不到「这一代里谁提供了 `cv2`、凭什么装它、装后元数据看到它是什么来源、在哪一代」。

## 决策

### 一、授权不变量：观察只能用于披露，不能单独决定装什么

装哪个发行包只有三条授权通道，且仍是 `depresolve.INSTALLABLE_SOURCES` 那三档：**项目声明**、**Tavotto 的表**、**用户确认的指定**（`user_specified`，#933）。没有第四档「观察」。

`depplan` 读观察（`ImportClass.distribution_candidates`）时，结果只能流向**披露字段** `JointPlan.provider_decisions`；不得流进 `requirements` / `constraints` / `missing` / `identity` / `inputs_digest` / 影响摘要。
`deprepair` 与 `depresolve` 仍不读观察字段（`test_the_installer_and_the_resolver_do_not_read_the_new_observation` 照旧；回执用的来源种类是装后**重新读**的静态索引，经 `depplan.static_index`，不是计划期的观察）。

原来的阶段性看护只能说「安装模块不碰这些名字」（AST 字符串断言）；现在补上**行为**看护：同一个项目，有观察与没有观察（`dists=None`），`requirements` / `identity` / `status` 逐字相同
（`tests/test_dependency_origin_install.py::TestObservedProviders::test_observation_never_changes_what_is_installed`，四个 provider 各一遍）。AST 那条保留，不是替换成它。

### 二、`provider_decisions`：每个提供者决策的依据与观察

`{import_name, distribution, basis, action, observed:[{distribution, version, provenance, reproducible}], replaces_observed}`。`basis` ∈ 三档授权通道；`action` ∈ install / satisfied / undecided；
`observed` 是此刻跑脚本的环境里**未被遮蔽**的已装提供者，editable / 本地 wheel / VCS 照样列出并标 `reproducible=false`，**从不**折成 `name==version`；`replaces_observed` = 新一代装的发行包与观察到的都不同。
只在「有观察」或「该 import 有互斥备选发行包」时出现；可选字段，`plan_version` 不升，不带路径与文件内容。

同一处顺带修正：互斥 provider 被声明仲裁替掉表里的名字时，`missing` / `install_only` 条目的 `resolution_source` 是 `project_declared`（原来仍写 `curated`，回执会把「声明选的」记成「表选的」）。

### 三、互斥 provider 的切换：别的作用域仍要求时，是 `dependency_scope_conflict`

`deprepair._scope_conflicts` 多一类：本次要装的 provider 会替掉账上**别的作用域**记着的互斥 provider → 冲突项 `{name: 被替掉的, installed_for, wanted_for, via: "provider", replaced_by}`，
沿用既有的两条出路（换成本作用域 / 子目录当独立项目）与「换成本作用域」自己的影响摘要（`drops` 里有被替掉的 provider）。同一作用域里用户改自己的声明仍是正常换 provider（P02）；没有归属的老账不参与（D04 的约定）。
失败不替换旧 active、不原地卸载旧代里的包（沿用代事务，用例钉着）。

### 四、成功回执：`result.providers`

装后核验（`_verify_distributions`）本来就重新读目标环境的静态索引；现在它把「这一代里实际提供这些 import 的发行包」返回，写进 `result.providers`：
`{import_name, distribution, version, source(resolution_source), provenance(装后元数据看到的来源种类), generation}`。读不出的 `provenance` 留空，不编造。

## 不做（剩余，交给后续）

* **editable / VCS / 本地 wheel 提供者在新一代里怎么办**：现状（Codex #920 P1 钉住的用例）是新一代照表装 PyPI 名，如实披露 `replaces_observed=true`。更严的策略（来源无法重建时拦住、让用户选「沿用原环境」）要新增用户动作与界面，本 PR 不动。
* **cv2 歧义的一项具体选择**：两个 provider 都装着且项目没点名时，`origins` 仍是 `ambiguous`，没有可点的选择，也没有把用户选择持久化到项目。需要前端 + 持久化设计（涉及 `user_specified` 目前只收纯表外名），另起。
* 观察到的**版本**不用于重建：账本仍记装后的 resolved version（ADR 0019 §九），不由原环境的版本反推。
* `import_error` 的装载诊断出口与「同 requirement 不重装」沿用现有 `dependency_import_still_failed` 与 `_attempted`（DEP-14 已有边界，本 PR 只加了回归对照）。
