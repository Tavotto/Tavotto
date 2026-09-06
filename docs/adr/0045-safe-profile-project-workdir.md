# ADR 0045：safe 档的工作目录可选脚本目录——项目级开关「在脚本目录里运行」

日期：2026-09-06 · 状态：**Accepted**

## 背景

worker 把 cwd 切到会话沙盒是 safe 档的写入边界：脚本用相对路径写出 / 删除的东西不碰
真实图库。为了让 `pd.read_csv("data.csv")` 这类相对读仍然成立，`figcapture` 给只读的
`open` 装了回退到脚本目录的逻辑（`builtins.open` / `io.open` / `Path.open` 三个入口）。

真实用户数据（2026-09-06，`2d 处理/` 的九个 ovito 脚本）暴露了这条回退的盲区：脚本用
`os.path.exists("1/etch_5-5.lammpstrj")`、`glob("./**/*.lammpstrj")` 判数据在不在，再交给
ovito 的 C++ `import_file` 读——三样都不经 Python 的 `open`。沙盒 cwd 下它们全部
「未找到」，脚本一张图都不画，界面报「stem 不存在」（#298 之后报「脚本跑完没出图」）。
**脚本永远不改**——用户就是典型用户，脚本要改才能出图等于产品不兼容这类脚本。

补回退覆盖面（`exists` / `stat` / `glob` / `listdir`）救不了 C++ 读取器；把 native 档
做进池被 ADR 0021 §1（桌面 sidecar 不得 spawn 用户 Python）与 §9.4（native 恒禁写回）
两条硬约束否掉。剩下唯一根治：**让脚本在自己的目录里跑**。

## 决策

### 一、safe 档多一个维度 `cwd_mode`，其余一字不动

`execspec.ExecutionSpec` 新增 `cwd_mode ∈ {sandbox, project}`（进 `STABLE_FIELDS`：它
改变脚本看到的世界，是语义不是路径）与 `sandbox`（机器相关，写入边界目录）。
`safe_spec(cwd_mode=project)` 把 `cwd` 换成脚本所在目录，`worker_argv` 只在这个模式下
多两个 token `--cwd <脚本目录>`——**默认模式的 argv 逐字节不变**（golden 用例钉着）。
worker 收到 `--cwd` 就 `chdir` 到那里，并且**不装**相对路径只读回退（相对路径本来就指向
项目）。

不变的清单，每一条都有用例：解释器链（ADR 0018 / 0044）、savefig 捕获不落盘、
`Path.unlink` / `Path.write_text` 守卫、写回（one_shot 重放取同一个模式）、沙盒目录仍存在
但脚本不往里写。

### 二、变的只有一条，而且要如实说

**脚本用相对路径写的中间文件会像终端里一样落进项目目录**（`open("cache/x.txt","w")`、
`np.save`、`to_csv`、C++ 写入器）。这是这个模式的定义，不是漏洞：用户在终端里跑同一个
脚本得到的就是这些文件。文案与机制逐条一致（ADR 0014 的纪律）：确认框写明「读得到 /
写出的落进项目 / Tavotto 仍不替它保存图片、不删不改已有文件 / 只对这个项目生效」。

### 三、项目级开关，首次开启确认一次

`engine/workdir.py` 是模式的唯一出处：存项目设置 `workdir.mode`，不写全局（A 项目的
脚本形状不该决定 B 项目的写入边界）；不认识的值当默认（设置文件被手改坏了，写入边界
不许悄悄消失）。`PATCH /api/engine/workdir {mode}` 改了就 `pool.shutdown_all(root)`——
cwd 是 spawn 时定下的，活着的会话还端着旧目录。三条 spawn 路径（Python 池、workerd
spawn 规格、one_shot）从同一个出处取模式。

界面：设置 → 渲染环境多一行开关；「脚本跑完没出图」（`no_figures_captured*`）的错误块
直接给「改为在脚本目录里运行」的入口——从失败到修好一步。切换成功后把这些面板重新排上。

### 四、这不是 native

进程仍是 Tavotto 自己起的 safe worker：ADR 0021 §1 的所有权约束一个字没动，ADR 0014
「不把 safe 的守卫悄悄搬进 native」也没动——这里是 safe 档自己多了一个开关。native 的
`cwd` 是用户的，没有 `cwd_mode` 这个维度（构造时拒绝）。

## 不做的事

* 不自动切换：零捕获时只给入口，不替用户决定（那是他项目里的写入边界）。
* 不扩回退覆盖面到 `exists` / `glob`：救不了 C++ 读取器，只会让脚本「以为」数据在。
* 不改根 AGENTS 的「守卫不放松」：守卫原样；变的是 cwd，且是用户按项目显式开的。

## 看护

`tests/test_workdir_mode.py`：argv 只多 `--cwd`、`cwd_mode` 进 stable payload、老 payload
读回默认、native 拒绝；开关项目级 / 不写全局 / 坏值当默认；真 worker 沙盒模式零张图 vs
项目模式 exists / glob / listdir / 相对 open 全成立且图被捕获、相对写落进项目、守卫拦住
删除、savefig 不落盘、沙盒空；三条 spawn 路径同源；端点 400 码 + 切换重建会话。
`web/src/components/WorkdirRow.test.tsx`：开启先确认、取消不改、成功重排「没出图」面板、
关闭不确认、建议只在沙盒模式出现、英文无中文泄漏。
