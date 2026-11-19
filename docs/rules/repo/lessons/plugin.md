# 错题本 · plugin

> 格式与规矩见 `docs/rules/repo/lessons/README.md`。写入只走每日复盘，别直接改。

### L-115 openai.yaml 形状写错只会被静默忽略，要用真 CLI 安装验证
- 类: 照这样
- 触发: codex-plugin/skills/tavotto-figure/agents/openai.yaml
- Seen: 2026-08-25
- 为什么: Codex 加载 agents/openai.yaml 是 fail open，形状错只 warning 并整份丢弃；依赖按 command 匹配已装 server，不按 value。
- 证据: #118

### L-116 动受管生成物前先枚举全部消费路径，含直接从仓库安装
- 类: 别这样
- 触发: scripts/build_mcp_widget.py, codex-plugin/mcp/tavotto_mcp/widget.py
- Seen: 2026-08-30
- 为什么: 以「开发和测试都是现建的」为由把画布产物移出版本库，会让直接从仓库安装插件的用户拿到没有画布的插件，服务端降级成缺失而零报错，门禁全绿。
- 证据: （迁移自维护者记录，无编号）

### L-117 跑真 stdio MCP server 只用 TAVOTTO_MCP_PYTHON 选环境
- 类: 别这样
- 触发: tests/test_mcp_resolver.py, codex-plugin/mcp/tavotto_mcp/bridge.py
- Seen: 2026-09-23
- 为什么: 给 server 设 PYTHONPATH 同样影响启动器自己的解释器，resolver 选到缺 PyMuPDF 的解释器，PDF 导出全假红；进程内又复现不了，浪费数轮。
- 证据: （迁移自维护者记录，无编号）

### L-133 路径边界判内核走过的每个对象，同类绕过第二次就换结构
- 类: 别这样
- 触发: codex-plugin/mcp/tavotto_mcp/roots.py, tests/test_mcp_path_walk_property.py, tests/test_mcp_local_scope.py, tests/test_mcp_write_tripwire.py, src/tavotto/engine/hostconnect.py
- Seen: 2026-11-05
- 为什么: 字面折叠后、realpath 后都只是两个点；凭据判定被连续绕过三轮，逐例修补时第二轮还引入回归。改逐段解析，并用随机目录树性质测试（判据独立、先拿内核核对）守一类漏洞；生成器要变异验证真造得出目标形状。
- 证据: #1757

### L-163 给会话加落盘状态，按「事务提交、回滚、进程换过只剩落盘」三种时刻各验一次
- 类: 别这样
- 触发: codex-plugin/mcp/tavotto_mcp/bridge.py, codex-plugin/mcp/tavotto_mcp/server.py, tests/test_mcp_fix_to_journal.py
- Seen: 2026-11-14
- 为什么: 新增的导出标记在整笔事务判定前就写盘，导出部分失败回滚只回内存，重启后恢复出失败那版；关会话的新提醒只看内存，进程换过后会话只在落盘记录里，提醒根本走不到。评审报一条 P1、一条 P2，合并组还踢了一次。
- 证据: #1903

### L-168 替换会话的落盘记录先写新、确认写成再删旧；恢复时跨来源按保存时间挑最新
- 类: 别这样
- 触发: codex-plugin/mcp/tavotto_mcp/sessionjournal.py, codex-plugin/mcp/tavotto_mcp/bridge.py, codex-plugin/mcp/tavotto_mcp/server.py
- Seen: 2026-11-18
- 为什么: 草稿恢复在新记录写盘失败（吞掉异常）后照删旧草稿与来源记录，关会话又先删旧草稿再写新的，磁盘满时唯一一份修改没了；同一张图有草稿也有更新的孤儿记录时总挑草稿，新改动静默丢。评审四条 P1。写盘失败要能被调用方看见，用例摆「写失败」与「两处都有记录」。一步交付的流水线在草稿恢复失败后照样改图、导出，提交导出时删了存着的修改，重试也绕不过，又两条 P1（#2008）：恢复失败就停下，别在只剩脚本的会话上往下走。
- 证据: #2007, #2008
