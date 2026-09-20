# U04 · 一次准备多个依赖，不等私有Python下载器

**前置 milestone：** U03.existing_open。先读总提示词及当前有效handoff；遵循03门禁政策。


使用已有合格base Python完成本阶段；U05随后补base来源。复用depresolve/deprepair/managedenv/envlease，不能建立第二个安装锁。

## 实际实现

以成熟解析器保留PEP508的name、specifier、extras、marker与选中group；requirements -r/constraints在授权项目内有界读取，循环/越界失败。不要把旧简化dict当完整依赖。PEP723/pyproject支持由实际元数据范围决定，未知格式显式unsupported，不视为无要求。

区分stdlib、本地模块、第三方、可选/TYPE_CHECKING和动态未知。标准路径联合求解项目约束+scientific adapter约束；默认wheels-only/批准源，未知私有名不去公网试装。完整未验证Poetry/pixi锁转换后置X01，但不能把^或marker剥掉偷偷继续。

在最终版本目录创建新环境并标incomplete；真实安装、依赖一致性、关键import、worker自检都通过再切active。旧环境保持到lease释放。不是把已创建venv从tmp rename过去，也不是原地往所有项目共享site-packages写包。

计划绑定项目/完整要求/环境/源/授权/版本。一次“准备并打开”可以覆盖完整已知计划，不能覆盖后发现的私有源/构建/用户环境修改。动态遗漏有界重新计划；普通ValueError不触发装包。取消不留下假ready，不为安装杀native。

## 测试

用小型固定wheelhouse/本地供应服务做真实多包联合安装。marker不适用、未选group不安装；本地lab_utils不误装；冲突/无wheel/坏hash/取消/自测失败不切active。事前验证缺包真不在目标，harness不得预装它。

检查依赖包/配置没有未经授权变化，合法.pyc/cache按预声明白名单单列。两个项目包版本冲突各自独立；实际原生扩展验证放集成lane，不把纯Python安装成功当ABI资格。

## 出口

已有base条件下正常UI/HTTP/MCP“准备并打开”成功，经真实worker产图并编辑；不要求U05已经能下载Python。对不支持声明有清楚动作，无无限缺包循环。
