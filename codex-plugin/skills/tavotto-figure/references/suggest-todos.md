# 把建议列成待办，用户勾好再改

用户问「这张图有什么可以改进的」「帮我看看」时，**先回答**：逐条编号说问题和建议，不动图。
用户接着说「按你的建议改」「改第一和第三条」，或一开始就说得很笼统（「改好看一点」），才用这三个工具。
具体的单条修改（「图例挪到左上」）直接 `tavotto_apply_overrides`，不用列待办。

## 三步

1. `tavotto_suggest_plan`：把你的建议交进来，每条一个待办：
   - `label`：一句话说做什么，读图的人看得懂（「纵轴标题加单位 (min)」），不写参数名、gid；
   - `why`：一个短分句说为什么，可省；
   - `kind`：`figure` = 图内能改（带 `changes: [{gid, prop, value}]`，词汇与 `tavotto_apply_overrides` 相同）；
     `script` = 要改脚本 / 数据（不带 `changes`）。
   - 有用户原话就带 `user_request`：用户没提尺寸时，改图幅的修改会被拦下。

   不写会话。回 `todos`（每条带 `key`）、`results`（交进来的每条落成了什么）和一张勾满时的修改后图。
2. 把 `todos` **逐条列给用户**，问要改哪几条（默认都改）。`results` 里 `ok: false` 的照实告诉用户原因
   （会藏掉数据、用户没要求改尺寸、连续曲线逐点加标记、图里没有这个元素、改了看不出变化……），不要偷偷换个改法再交。
3. 用户点头后 `tavotto_suggest_apply({session_id, plan_id, keys})`：图内的那些一次写进会话；
   `script_todos` 里的由你接着改脚本——改前告诉用户要改什么，改完 `.py` 下一次调用会按新脚本重跑同一个会话。
   改了画出来的数据（换算单位、过滤点）先问用户。

`tavotto_suggest_status` 只读：方案应用了没有、用了哪几条、出方案之后图有没有被改过（`stale`）。

## 守卫与拒绝

- 与 Tavotto 改图助手同一套服务端守卫：坐标范围比数据窄（`hides_data`）、用户没要求却改图幅（`size_locked`）、
  连续曲线（> 40 点）逐点加标记（`dense_curve`）都会被拦下；Unicode 上下标（⁻¹、₂）会自动换成 mathtext。
- 勾满时带出新的渲染 warning：整份方案不出（`change_plan_render_failed`），不指出是哪一条——拆小几条再交。
- 出方案之后图被改过（脚本或图内修改）：`suggest_apply` 回 `suggest_plan_stale`，重新出方案再问用户。
- 方案只认出它的那个会话；关了会话、过了 6 小时、或别的会话拿来用都是 `suggest_plan_unknown`。
