# 发布 action 的 Node 24 迁移核验（PR #798）

## 范围与来源

2026-10-07 从各 action 的官方仓库解析对应 major tag（annotated tag 继续解到 commit），
逐项读取下列不可变提交的 `action.yml`，确认 `runs.using: node24`。发布 workflow
保留 SHA pin，不以可移动的 major tag 代替原有供应链约束。将来升级 pin 时须重新核上游
runtime / 输入兼容性，并同步 `tests/test_release_workflow_contract.py` 的核验清单。

| Action | Major | 核验的 action.yml |
| --- | --- | --- |
| actions/checkout | v5 | [fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09](https://github.com/actions/checkout/blob/fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09/action.yml) |
| actions/setup-python | v6 | [ece7cb06caefa5fff74198d8649806c4678c61a1](https://github.com/actions/setup-python/blob/ece7cb06caefa5fff74198d8649806c4678c61a1/action.yml) |
| actions/setup-node | v6 | [249970729cb0ef3589644e2896645e5dc5ba9c38](https://github.com/actions/setup-node/blob/249970729cb0ef3589644e2896645e5dc5ba9c38/action.yml) |
| actions/cache/restore | v5 | [caa296126883cff596d87d8935842f9db880ef25](https://github.com/actions/cache/blob/caa296126883cff596d87d8935842f9db880ef25/restore/action.yml) |
| actions/upload-artifact | v6 | [b7c566a772e6b6bfb58ed0dc250532a479d7789f](https://github.com/actions/upload-artifact/blob/b7c566a772e6b6bfb58ed0dc250532a479d7789f/action.yml) |
| actions/download-artifact | v7 | [37930b1c2abaa49bbe596cd826c3c89aef350131](https://github.com/actions/download-artifact/blob/37930b1c2abaa49bbe596cd826c3c89aef350131/action.yml) |
| pnpm/action-setup | v6 | [0977fd99725f1db4007ccb2928dbb4e90d06cc86](https://github.com/pnpm/action-setup/blob/0977fd99725f1db4007ccb2928dbb4e90d06cc86/action.yml) |
| softprops/action-gh-release | v3 | [efb35369e0ad2afab669f228072c1b0d510eae64](https://github.com/softprops/action-gh-release/blob/efb35369e0ad2afab669f228072c1b0d510eae64/action.yml) |
| signpath/github-action-submit-signing-request | v2 | [c92b958760219087e01f8d67a1669ed57afe2627](https://github.com/signpath/github-action-submit-signing-request/blob/c92b958760219087e01f8d67a1669ed57afe2627/action.yml) |

初始迁移更新 `release.yml`、`release-publish.yml`、`desktop-tauri.yml` 的 action 版本，
将它们及 `lab-ci.yml` 的 13 个 Linux hosted job 固定为 `ubuntu-24.04`。
SignPath v2 在 main 已是上述 Node 24 commit，原 pin 原样保留。
`lab-ci.yml` 的 checkout、其它 workflow、macOS / Windows 矩阵、项目的 Node 22 / Python 3.13 /
pnpm 11.27.1 工具链版本均不属于此次迁移。
后续 alert #217 引出的两个限定加固见下节，不再把最终补丁声称为纯版本替换。

## 兼容性核验

- checkout v5 与 setup-* 的 Node 24 runtime 要求 GitHub Actions runner 至少 2.327.1；
  cache v5 要求至少 2.327.1。这些步骤只在 GitHub hosted runner 执行，本次不改变 lab runner。
- [download-artifact v7](https://github.com/actions/download-artifact/blob/37930b1c2abaa49bbe596cd826c3c89aef350131/README.md)
  按 name 下载仍直接落在 path；完整 pattern 集合仍按 artifact 名建子目录。
  v7 将只有一项的 pattern / ID 结果直接解到 path，而本链的 desktop / manifest 集合分别
  必须有 3 / 4 项：既有按名字点名的步骤会拒绝残缺结果，不能靠 action 的 success 越过。
  updater-manifest 的 pattern 消费者仍由三平台 `--require` 判据拒绝不完整结果。
  upload v6 与 download v7 仍使用 v4+ artifact 后端，既有跨 run 的 run-id / token 输入不变。
- [pnpm action v6 的安装实现](https://github.com/pnpm/action-setup/blob/0977fd99725f1db4007ccb2928dbb4e90d06cc86/src/install-pnpm/run.ts)
  仍支持明确的 11.27.1 输入，核验目标版本后 self-update；不采用其后继 action，
  不改变显式 pnpm 缓存和安装步骤。
- [softprops v3](https://github.com/softprops/action-gh-release/blob/efb35369e0ad2afab669f228072c1b0d510eae64/README.md)
  保留本链使用的 tag_name、target_commitish、draft、body_path、generate_release_notes 与 files 输入；
  正式发布先上传再发布。没有新增 token、权限、签名输入或隐式批准。

## Alert #217：消费者只恢复缓存，调用者只能传精确 commit

`actions/cache-poisoning/direct-cache` 指向 desktop `build` 的指定源码 checkout →
`build/runtime-cache` 写回。原组合 cache action 在成功 job 的 post 阶段保存目录；
workflow 在 main 上 dispatch 时，缓存写入的是 workflow ref 的作用域，不能把
「checkout 选了另一个 SHA」误当成缓存作用域隔离。PR merge-ref 的缓存不会反向进入
main。规则见 [GitHub cache scope](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#restrictions-for-accessing-a-cache)。

现有 release caller 已传解析后的 SHA，desktop 自己也验 main ancestry，没有复现从
不受信任的公开 PR 直接投毒的入口，不能由告警推断已遭利用。但可复用接口原来接受
branch、缩写、revision 表达式并原样输出；临时 Git 仓库实跑原 trust shell 证明
branch 在检查后可移动，后续 checkout 的身份不再必然等于被检查的身份。

因此做两项实质防御，不 dismiss、不改 CodeQL 规则或检查：

1. `inputs.sha` 必须是 40 位小写十六进制且对象类型确为 commit，再经过原 ancestry
   判断。拒绝 branch、缩写、表达式、tag object、blob、不存在及未合并的 commit；
   standalone 的 `inputs.tag` 路径仍先解析成 commit，再验 ancestry，合法 tag 用法不变
2. CPython 归档改为相同 pin 的 `actions/cache/restore`。官方
   [metadata](https://github.com/actions/cache/blob/caa296126883cff596d87d8935842f9db880ef25/restore/action.yml)
   声明 Node 24、只有 restore-only main、没有 save post；
   [实现](https://github.com/actions/cache/blob/caa296126883cff596d87d8935842f9db880ef25/src/restoreImpl.ts)
   只调用 restoreCache。path/key 与既有 CI cache-seed 逐字段一致，不加 restore-keys、
   不要求 hit，不条件跳过构建。默认分支种子仍由原 CI 维护；缺少种子（包括 Intel
   首跑）只增加本 job 下载，校验/构建照常。发布消费者不再保存新 CPython cache

这不是收紧整个 job 的 cache token 权限：pnpm / Rust 缓存保持原样；消除的是被报告的
CPython 目录自动写回。当前 [CodeQL cache writer model](https://github.com/github/codeql/blob/36994cdec4ffab77993ac386c85cf551e28f552f/actions/ql/lib/codeql/actions/security/CachePoisoningQuery.qll)
把组合 cache 与 cache/save 识别为 writer；restore-only 没有写行为，最终告警状态仍以
新 head 的实际扫描为准，不能用查询模型阅读代替重扫。

安全本地验证只用临时 Git、惰性文本文件及被替身拦截的下载：原下载器在 miss 时下载
并核 SHA、有效 hit 时离线复用、坏 hit 时重新下载并核 SHA、坏下载时报错且清除半成品。
没有读写生产缓存、提取凭据或运行发布。测试真实运行 workflow 的 trust shell，并看护
restore-only、种子 key/path 对齐、构建无条件执行；删除新守卫或改回组合 cache 必须红。

## 验证边界

初始迁移相对集成 main 的四条 workflow，在去掉 action 版本与 Linux runner 值后
逐字节相同。最终补丁另外包含上节的精确 commit 守卫与 CPython restore-only；
两段发布、lab 身份、清单/checksum、签名、上传、发布条件与权限仍没有改动。
已发行的源码、tag、Release 与资产不变。

合同测试核对精确 `uses` 字段与 13 个 Linux job 的 runner 值；测试不联网，不以注释
里的版本声明替代真实字段。反证包括浮动 tag、旧 Node 20 pin 与 ubuntu-latest 回退。
本地合同 / actionlint 与 PR full-ci 不是新版本的实际发版演练；本 PR 不触发 Release /
PyPI / 签名 / lab 发布。下次发版仍须按原规则在新的源码上做 publish=false 演练，不能
复用 v0.18.0 的发布执行记录作为新 workflow 的动态证据。
