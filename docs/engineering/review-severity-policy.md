# 缺陷分级与「挡不挡发布」的判定规则

> **分级回答的是「它挡不挡这次发布」，不是「它重不重要」。**
> 这两件事经常不一致：一个很重要的架构问题可以不挡 1.0（§4 的 backlog 全是），
> 一个很小的 P2 可以挡（它会让用户拿着一张错的图去投稿）。
>
> 这份文档是**唯一权威**。`docs/1.0-release-readiness.md` §2 是它的摘要，
> `.github/labels.yml` 的 description 是它的一行版，
> `.github/ISSUE_TEMPLATE/p2.yml` 按它提问。三处不一致时以本文件为准。

---

## 1. severity —— 缺陷本身有多严重

### `severity:P0` —— 必须阻塞

- 数据损坏
- 静默写坏用户源文件
- 安全边界失效
- 项目打不开
- crash / 启动阻塞
- 用户的工作无法恢复

### `severity:P1` —— 必须阻塞

- 受支持的 workflow 用不了
- 受支持的 OS / runtime 不工作
- **silent wrong**：界面说成功、实际无效或结果错误
- undo / replay 错误
- 浏览器与桌面对同一脚本产生**语义分叉**
- **release CI 门禁空转**——报平安的门禁比没有门禁更坏
- installer / runtime / package 不工作

### `severity:P2` —— 缺陷成立，是否阻塞**单独裁决**

这是绝大多数发现落的档（本仓库 188 条 Codex 发现里 151 条是 P2）。
**P2 不自动等于「可以放行」，也不自动等于「必须修」**——按 §2 的九条判据裁决。

### `severity:P3` —— 打磨

不阻塞任何发布，也不进 fix train，除非顺手。

---

## 2. `release:blocker` —— 人工升级，**不由 severity 自动推出**

任何 P2 只要命中下面**任意一条**，人工加上 `release:blocker` 标签：

1. **静默产生错误的科研图**
2. **画面与 manifest / patch 声明不一致**
3. 写坏或**可能**写坏源文件、PDF、SVG、PNG、项目状态
4. 安全边界失效
5. 核心黄金路径无法完成（安装 → 打开 → 编辑 → 撤销 → 导出）
6. 正式支持平台无法安装、启动、导出或升级
7. release、SBOM、provenance、updater、PyPI、安装包发布无法完成
8. **由当前 PR 直接引入**
9. 产品宣称 full support，但用户入口实际不可达；
   或**错误能力仍出现在 UI 中，让用户误以为已经生效**

> 判 P2 归哪一边，最有用的一问是：
> **用户会不会拿着一个错的结果继续往下走，而中间没有任何提示？**
> 会 → `release:blocker`。

### 2.1 可以不阻塞 v1.0 的 P2（但仍必须被处理）

同时满足**全部**七条：

- 低频
- **失败结果可见**（用户看得见发生了什么）
- 不会破坏用户数据
- 有安全的替代路径
- 有明确、写得出来的触发条件
- 有 milestone
- 有回归边界测试，且 **UI 不会继续声称那个错误能力**

少一条都不算。特别是最后一条：一个「点了没反应」的按钮留在界面上，
本身就命中 §2 第 9 条。

---

## 3. 深层 P2 的标准处理顺序

**不要在 1.0 稳定期重写核心模型。** 顺序是：

```
detect → guard / hide → unsupported reason → issue → v1.1 architecture fix
```

每一步都要落成代码，不是落成一句话：

| 步 | 落成什么 | 反面例子 |
|---|---|---|
| detect | 一个能判出「这次命中了」的谓词，有单测 | 「一般不会遇到」 |
| guard / hide | 该能力不再出现在 UI / 不再进能力表 | 留着按钮，文档里写「已知问题」 |
| unsupported reason | manifest 里一条**稳定**的 reason 字符串 | 只在日志里 |
| issue | 带 milestone + acceptance test 的 open issue | 只写进某份 md |
| v1.1 fix | 排进 `disposition:minor-release` | 「以后再说」 |

**范例（本轮实做）：多宿主色条的方向切换。**
`fig.colorbar(im, ax=[ax1, ax2])` 的色条横跨两个子图，而我们只记第一个宿主，
翻转方向时会被缩到一图宽（`docs/1.0-release-readiness.md` §4.8 有实测数字）。
完整修法要把宿主从**一个 axes** 改成**一组 axes**，`_cb_place` /
`_cb_target_rect` / `axes_follow` 三处按并集算——那是色条落位模型的改动，
1.0 稳定期明确不做。于是：

- **detect**：`colorbar_hosts()` 数宿主个数；
- **guard**：多宿主时不再登记 `orientation` 能力；
- **reason**：manifest 给 `multi_host_colorbar`；
- **issue**：`disposition:minor-release` + milestone `v1.1`；
- **测试**：确保它不会退回「能点但布局错误」那个状态。

---

## 4. 这份规则用在哪三个地方

1. **issue** —— `.github/ISSUE_TEMPLATE/p2.yml` 逐条问，
   `disposition` 与 `severity` 落成标签（`.github/labels.yml`）。
2. **PR** —— `.github/PULL_REQUEST_TEMPLATE.md` 要求列出本 PR 的
   P0/P1/P2 disposition 与延后 P2 的 issue 号。
3. **Codex review thread** —— `docs/engineering/codex-review-policy.md`
   规定每条 thread 合并前必须有 disposition，
   `scripts/ci/codex_review_gate.py` 把 unresolved P0/P1 变成 check failure。

---

## 5. 不要求的事

- **不要求 review 永远零发现。** Codex 总能再找出一条低频 P2；
  「等到零发现再发」等于永远不发。判据是「剩余风险被分类、被限制、被排期」。
- **不要求每个 matplotlib artist 都支持。** 不支持要**说出来**
  （`unsupported` + reason），不能静默丢掉。
- **不要求架构达到最终形态。** `docs/1.0-release-readiness.md` §4 那批
  backlog 全部保留到 1.0 之后。
