# ADR 0046：build 超时按注册表的 cost 分档

日期：2026-09-07 · 状态：**Accepted**

## 背景

`pool.BUILD_TIMEOUT` 是一刀切的 900 秒。注册表早就把脚本分成 light（秒级）/ medium
（十秒级）/ heavy（分钟级），界面也按它提示「冷启动可能需要几分钟」，但超时不看它。
真实脚本：九条 200–280 MB 的轨迹各算 100 帧邻居搜索（ADR 0045 的那个目录），在 15 分钟
内跑不完，而它没有死循环。脚本永远不改，超时该跟着脚本的本性走。

## 决策

* `BUILD_TIMEOUT` 保留为 **medium 档的基数**；`BUILD_TIMEOUT_FACTORS = {light: ⅓,
  medium: 1, heavy: 4}`，`build_timeout_for(cost)` 按倍数缩放。倍数而不是三个常量：
  用例把基数 monkeypatch 成 2 秒时各档一起缩。
* cost 从**磁盘上的注册表**现读（`script_cost`）：pool 不 import app，注册表随图库走、
  多项目并存，按 (项目, 脚本) 现读最不会拿错；没有注册表 / 没登记 / 一次性重放一律 medium。
* 两条控制面（Python 池 / workerd）的 `ensure_built` 同一条分档；超时文案与
  `errors:backend.worker_timeout` 都告诉用户「本来就要跑很久的标 heavy」。
* light 缩到 5 分钟是有意的：秒级脚本卡 15 分钟才报，等于把死循环当正常。

## 不做的事

* 不做可配置的自由数值：三档已经是注册表的既有词汇，界面上现成能改；再加一个数字等于
  第二套语义。
* 不动 override / export / native 会话的超时。

## 看护

`tests/test_build_timeout_tiers.py`：倍数与基数缩放、注册表读取（缺失 / 未登记 / 坏值 →
medium）、两条控制面 build 请求真的带上分档超时。
