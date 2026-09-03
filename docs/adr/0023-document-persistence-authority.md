# ADR 0023：文档落盘的唯一权威（原子写、非有限数、收纳目录的边界）

状态：**Accepted**
日期：2026-08-29
相关：[0001 项目/画布/标签/对象层级](0001-project-canvas-tab-object.md)（文档模型）、
[0009 写回像素验证](0009-write-back-pixel-verification.md)（另一条写盘链路，
它管的是**用户源文件**，本条管的是**Tavotto 自己的文档**）、
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| Python 侧文档类写入 | 只有 `engine/atomicio.py` 一份实现 |
| 写入序列 | tmp（同目录）→ flush → **fsync 文件** → `os.replace` → fsync 目录 → 失败清 tmp |
| NaN / Infinity（写） | **序列化那一步就拒绝**（`allow_nan=False`），400 `non_finite_number` |
| NaN / Infinity（读） | `documents.loads_document()` 的 `parse_constant`，400 `non_finite_on_disk`（2026-09-03 补，见 §3.2a） |
| schema 判据 | `engine/documents.py` 一份；更高版本 → `schema_too_new`，不"尽力打开" |
| 收纳目录里 Tavotto 自己的文件 | **枚举**（`RESERVED_DOCUMENT_FILENAMES`），不用 `_` 前缀规则 |
| 修订号 | 内容 hash，**不掺 mtime** |
| `/api/layouts` 的 schema 校验 | 本次**不收紧**（见 §5） |

---

## 1. 背景：起始状态的九份实现

`ef9ac02` 上，Flask 父进程一侧的「原子写」有九份各自手写的副本：
`app.py` 四处（`_write_baked` / autosave / versions / styles），
`engine/` 里 `config.py`、`runspec.py`、`runtimeasset.py`、`locate.py`、
`session_client.py`、`nativehandoff.py` 各一处。

九份不是「重复代码」这么简单——**它们的行为不一样**：

- 只有 `runspec.py` 与 `nativehandoff.py` 做 `os.fsync`；
- 没有一份在失败时清掉临时文件；
- 没有一份返回结构化错误（调用方只能拿到一个 `OSError` 字符串）；
- 而 **`POST /api/layouts/<name>`——用户的「另存为」，产品里最显眼的一次
  保存——根本没有 tmp**，是 `write_text` 直接盖。写到一半失败留下的是一个
  截断的文件，而好的那一份已经被顶掉了。

于是「保存是不是原子的」这个问题在本仓库没有唯一答案，只有九个各自不同的答案。

## 2. 决定

**`src/tavotto/engine/atomicio.py` 是 Flask 父进程一侧文档类写入的唯一实现。**

```text
write_json(path, obj, indent=None)
  └─ dumps_json(obj)            # allow_nan=False，序列化在碰磁盘之前
  └─ write_bytes(path, data)
        1. mkdir -p parent
        2. 同目录临时文件（名字带 pid + 进程内序号）
        3. write + flush + os.fsync(fd)
        4. os.replace(tmp, path)
        5. fsync 目录（Windows 上打不开目录，忽略）
        6. 任何一步失败 → 清 tmp + 抛 AtomicWriteError(code, message, path)
```

`content_revision(path)` 给出内容修订号，供 Prompt 03 的外部修改检测当基线。

**`src/tavotto/engine/documents.py` 是文档格式判据的唯一实现**：schema 版本、
骨架校验、收纳目录里谁不是用户文档。

## 3. 理由（几条容易走反的分岔）

### 3.1 fsync 文件，不只是 replace

`os.replace` 保证的是「目录项要么指向旧 inode，要么指向新 inode」，
**不保证新 inode 的内容已经离开页缓存**。掉电时少了这一步，
replace 出来的是一个**空文件**——比保留旧内容还糟。

### 3.2 非有限数在写入边界拒绝，而不是写出去

`json.dumps` 默认 `allow_nan=True`，会写出 `NaN` / `Infinity` /`-Infinity`
这三个**不是 JSON** 的字面量。Python 自己读得回来，浏览器的 `JSON.parse`
读不动。落进 `_autosave/<doc>.json` 的后果是：这份文档在前端表现为"读不出来"，
静默退回本机兜底副本——用户看到的是"我的改动没了"，而磁盘上那份文件
看起来好端端的。**实测**（2026-08-29）：`PUT /api/autosave/d1` 带 `NaN` 回 200，
磁盘上就是 `{"w": NaN}`。

所以判据放在写入边界上并**响亮地失败**，而不是写一份没人能读的文件。

### 3.2a 读侧的另一半（2026-09-03，issue #222）

上面那条只挡住**我们自己写出去的**那一份。外部工具往 `tavottofile/*.json`
写一个 `NaN` 之后，Python 的 `json.loads` 照读不误（它默认认这三个非标准
字面量），而**每一份经过后端交给浏览器的字节都会在 `JSON.parse` 上炸掉**：
`GET /api/layouts/<name>` 原样 `send_file`、`GET /api/autosave/<id>` 原样发
字节、`POST /api/package/open` 把包里的 doc 直接 `jsonify` 回去。用户看到的
仍然是「这份文档打不开」，而磁盘上那个文件看起来好端端的——与 §3.2 要挡的
是同一个现象，只是这一次是别人写坏的。

于是读侧也有唯一入口 `documents.loads_document()`（`parse_constant`）。
**两侧不是两份权威，是同一条规则的两个边界**（与 patchspec / atomicio 的
关系同形）。code 刻意不同名：写侧说的是「你这次保存没写进去，磁盘上那份
一字未动」，读侧说的是「磁盘上那份是坏的，Tavotto 没有动它」——用户的下一步
不一样。

### 3.2b 第三个边界：响应

读侧闸挡的是磁盘上写着 `NaN` **字面量**那一档。还有第二条来路：磁盘上是合法
的 `1e400`，Python 读成 `inf`，而 `GET /api/versions/<id>/<vid>` 与
`POST /api/package/open` 交给浏览器的**不是磁盘上那份字节，是后端重新序列化
出来的响应**——Flask 默认 `allow_nan=True`，实测 `jsonify({"x": float("inf")})`
回的是 `{"x":Infinity}`，浏览器 `JSON.parse` 当场拒收。

所以同一条规则有**三个**边界，各守一处：写盘 `atomicio.dumps_json`、读盘
`documents.loads_document`、响应 `app._StrictJSONProvider`（`allow_nan=False`，
失败是 500 `internal_error`——发一个响亮的 500 好过发一份接收方解析不了的响应）。

`parse_float` **刻意不接管**：套在每一个浮点数上的 Python 回调会把整条读路径
拖慢（版本时间线整份读回正是热路径），而它要挡的那一档已经由响应边界管住了。
（本条第一版的理由写的是「两侧读到的是同一个值」——那是**量错了时刻**：它
描述读文件那一刻，而经过 `jsonify` 的那条路上前端根本没读过那个文件。）

两个消费点**有意**只把它当「读不出来」：`document_summary`（契约就是读不出来
→ `None`，它的两个调用方一个是 409 冲突响应的一部分、一个是 `/summary` 的
404，都不能抛）与 `_autosave_newer_than`（旧前端的兜底，既定纪律是不能因为
一个坏掉的旧槽位把用户锁死）。`_load_versions` 相反，**必须抛**：`DocumentError`
是 `ValueError` 的子类，跟着原来的 `except (OSError, ValueError): return []`
走的话时间线会显示成"没有版本"，而下一次创建检查点会在那个空列表上整份
写回——用户全部的检查点当场没了。

**这不是新规则，是把一条已经验证过的规则铺到第二个出口。** `engine/patchspec.py`
早就在做同一件事：规范化时把非有限浮点剔成 `non_finite_float`（第 64–66 行），
序列化再用 `allow_nan=False` 兜第二道（第 146–153 行），Rust 镜像
`workerd/src/pyfloat.rs` 同款。两者不是重复权威——patchspec 管的是
**补丁线格式**，atomicio 管的是**磁盘写入**；它们守同一条 JSON 有效性规则，
是因为两边产出的 JSON 都要交给别的解析器读。

### 3.3 收纳目录用枚举，不用 `_` 前缀规则

`GET /api/layouts` 对收纳目录 `glob("*.json")`，而样式预设就存在
`LAYOUT_DIR/_styles.json` —— 实测它会被列成一份叫 `_styles` 的"用户文档"，
点开是一份读不成画布的样式表；`POST /api/layouts/_styles` 还能用一份画布
把它整个盖掉。

修法看起来该是「下划线开头 = 内部文件」，**但那条规则会藏起用户的文档**：
画布名要过一遍 `[^\w\-一-鿿]+ → _` 的净化，`（图一）` 会变成 `_图一_`。
所以只认我们自己创建的那几个名字（`RESERVED_DOCUMENT_FILENAMES`），
新增内部文件时在那张表里加一行——加不进去的东西就不该放进收纳目录。

### 3.4 修订号只取内容 hash

修订号回答的是「内容变了没有」。把 mtime 拌进去，一次 `touch`、
一次从备份原样恢复、一次跨机器拷贝都会变出一个新修订号，
于是外部修改检测会对着一份**逐字节相同**的文件报冲突。
要「文件被动过没有」就单独去 `stat`，别把两个维度揉成一个数。

## 4. 后果

- 新增任何文档类写入点一律 `engine_atomicio.write_json(...)`；
  再抄一遍 tmp+replace 视为回退。
- `AtomicWriteError` / `DocumentError` 由 `app.py` 的两个 errorhandler 统一映射：
  载荷问题 400（`non_finite_number` / `invalid_document` / `schema_too_new`），
  名字被占用 409（`reserved_name`），磁盘/权限 500。
  code 一旦发布不能改名（见 `app.py` 错误码那段的约定）。
- 判据由 `tests/test_document_persistence.py` 看护，**十条变异逐一验过**
  （去掉 fsync / 放开 allow_nan / 不清 tmp / 退回非原子写 / 守卫空转 /
  修订号掺 mtime …），每一条都能把对应用例打红。

## 5. 明确没做的两件事（附条件）

**a) `/api/layouts/<name>` 的载荷不做 schema 校验。**
已经在用这条路的调用方不止前端：`scripts/ci/upgrade_acceptance.py` 发的是
`{"doc": ...}` 包一层的形状。在这次改动里收紧，会让 N-1 升级验收的两个检查
悄悄换一种坏法（它们**现在就已经是坏的**，见
`docs/implementation/product-ux-reliability/STATUS.md` 的 R-18）。
那属于修调用方，不属于修落盘。非有限数仍然挡——那种文档写出去谁都读不回来。
**条件**：R-18 修好后再收紧，并同时给 `/api/layouts` 补 round-trip 用例。
**已兑现（2026-09-02，Session 23）**：R-18 的调用方修好之后，`POST /api/layouts/<name>`
与自动保存共用 `validate_document`（不是文档 / 来自更新版本的 schema 都 400），
round-trip 与拒绝用例在 `tests/test_document_persistence.py`。

**b) 不给文档模型加 `extensions` 透传字段。**
Prompt 02 §四 提议留一个未知字段兜底位。但现在 `migrateToProject` 与
`validate_document` 都只认 schema 2/3，更高版本一律拒绝，所以"未知扩展字段"
今天不存在；现在加等于加一个没有写入方的抽象（与 1.0 收敛纪律相悖）。
**条件**：出现 schema 4，或出现需要在文档里寄存数据的插件时再加，
并在同一个改动里带上写入方与 round-trip 用例。
