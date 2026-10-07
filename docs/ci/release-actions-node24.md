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
| actions/cache | v5 | [caa296126883cff596d87d8935842f9db880ef25](https://github.com/actions/cache/blob/caa296126883cff596d87d8935842f9db880ef25/action.yml) |
| actions/upload-artifact | v6 | [b7c566a772e6b6bfb58ed0dc250532a479d7789f](https://github.com/actions/upload-artifact/blob/b7c566a772e6b6bfb58ed0dc250532a479d7789f/action.yml) |
| actions/download-artifact | v7 | [37930b1c2abaa49bbe596cd826c3c89aef350131](https://github.com/actions/download-artifact/blob/37930b1c2abaa49bbe596cd826c3c89aef350131/action.yml) |
| pnpm/action-setup | v6 | [0977fd99725f1db4007ccb2928dbb4e90d06cc86](https://github.com/pnpm/action-setup/blob/0977fd99725f1db4007ccb2928dbb4e90d06cc86/action.yml) |
| softprops/action-gh-release | v3 | [efb35369e0ad2afab669f228072c1b0d510eae64](https://github.com/softprops/action-gh-release/blob/efb35369e0ad2afab669f228072c1b0d510eae64/action.yml) |
| signpath/github-action-submit-signing-request | v2 | [c92b958760219087e01f8d67a1669ed57afe2627](https://github.com/signpath/github-action-submit-signing-request/blob/c92b958760219087e01f8d67a1669ed57afe2627/action.yml) |

只更新 `release.yml`、`release-publish.yml`、`desktop-tauri.yml` 的 action 版本，
将它们及 `lab-ci.yml` 的 13 个 Linux hosted job 固定为 `ubuntu-24.04`。
SignPath v2 在 main 已是上述 Node 24 commit，原 pin 原样保留。
`lab-ci.yml` 的 checkout、其它 workflow、macOS / Windows 矩阵、项目的 Node 22 / Python 3.13 /
pnpm 11.27.1 工具链版本均不属于此次迁移。

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

## 验证边界

相对集成的 main，去掉 action 的 `uses` 版本与 Linux `runs-on` 值后，四条 workflow
逐字节相同：受信 SHA checkout、两段发布、lab 身份、清单/checksum、签名、上传、发布
条件与权限没有改动。已发行的源码、tag、Release 与资产不变。

合同测试核对精确 `uses` 字段与 13 个 Linux job 的 runner 值；测试不联网，不以注释
里的版本声明替代真实字段。反证包括浮动 tag、旧 Node 20 pin 与 ubuntu-latest 回退。
本地合同 / actionlint 与 PR full-ci 不是新版本的实际发版演练；本 PR 不触发 Release /
PyPI / 签名 / lab 发布。下次发版仍须按原规则在新的源码上做 publish=false 演练，不能
复用 v0.18.0 的发布执行记录作为新 workflow 的动态证据。

