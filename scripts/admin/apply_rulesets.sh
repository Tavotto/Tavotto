#!/usr/bin/env bash
# GitHub ruleset 的备份 / 比对 / 还原 / 增删必需检查。
#
# **默认 dry-run。** 不给 --apply 就只打印将要发生什么，一个字节都不改。
#
# 为什么要有这个脚本：ruleset 是**唯一**挡住「直接推 main」和「必需检查没过
# 就合并」的东西。它一旦被改坏，症状不是报错，是**从此不再拦任何东西**——
# 与本仓库反复撞到的空门禁是同一个形状，而且这一道没有测试能覆盖。
# 所以：改之前必须先存档，改完必须能一条命令回去。
#
#   scripts/admin/apply_rulesets.sh --backup            # 存档当前远程配置
#   scripts/admin/apply_rulesets.sh --diff              # 远程 vs 存档
#   scripts/admin/apply_rulesets.sh --restore           # dry-run
#   scripts/admin/apply_rulesets.sh --restore --apply   # 真还原
#   scripts/admin/apply_rulesets.sh --add-check "gate"           # dry-run
#   scripts/admin/apply_rulesets.sh --add-check "gate" --apply
#
# **登记必需检查的顺序不能反**（docs/1.0-release-readiness.md §4.7）：
# 先让产出该 check 的 job 合进 main 并**真的产出过一次结论**，再登记。
# 反过来的话那个 check 永远不会出现，而规则要求它通过——整个仓库被锁死。
# --add-check 会先替你核对这一条，核对不过默认拒绝执行。

set -euo pipefail

REPO="${REPO:-Tavotto/Tavotto}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STORE="$HERE/docs/admin/rulesets"

APPLY=0; MODE=""; CHECK=""; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --apply)      APPLY=1 ;;
    --backup)     MODE="backup" ;;
    --diff)       MODE="diff" ;;
    --restore)    MODE="restore" ;;
    --add-check)  MODE="add-check"; CHECK="${2:?--add-check 要一个 check 名}"; shift ;;
    --rm-check)   MODE="rm-check";   CHECK="${2:?--rm-check 要一个 check 名}"; shift ;;
    --recreate)   MODE="recreate";   CHECK="${2:?--recreate 要一个存档 id}"; shift ;;
    --force)      FORCE=1 ;;
    --repo)       REPO="${2:?}"; shift ;;
    -h|--help)    sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "不认识的参数：$1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$MODE" ] || { sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 2; }

mkdir -p "$STORE"

ids() { gh api "repos/$REPO/rulesets" --jq '.[].id'; }

norm() {  # 稳定排序 + 去掉每次都变的易变字段，让 diff 只显示真实差异
  python3 -c '
import json,sys
d=json.load(sys.stdin)
for k in ("_links","node_id","created_at","updated_at","current_user_can_bypass"):
    d.pop(k,None)
json.dump(d,sys.stdout,indent=2,ensure_ascii=False,sort_keys=True)
print()'
}

case "$MODE" in
backup)
  # **一律临时文件 + mv，绝不直接 `> 存档`。**
  # `>` 在跑管道**之前**就把目标截成 0 字节：抓取失败、JSON 畸形、网络断，
  # 哪一样都会让 `pipefail` 退出——而那份唯一的回滚材料已经没了。
  # 这个脚本的全部意义就是「改保护之前先把能还原的东西存下来」，
  # 它自己把存档毁掉是最坏的一种失败。（与渲染缓存同一条纪律：
  # 写入临时文件 + 原子替换，见 CLAUDE.md「/api/render 的磁盘缓存键」。）
  for id in $(ids); do
    tmp="$STORE/.ruleset-$id.json.tmp"
    if gh api "repos/$REPO/rulesets/$id" | python3 -c '
import json,sys
json.dump(json.load(sys.stdin),sys.stdout,indent=2,ensure_ascii=False,sort_keys=True)' \
         > "$tmp"; then
      mv -f "$tmp" "$STORE/ruleset-$id.json"
      echo "存档 ruleset $id → docs/admin/rulesets/ruleset-$id.json"
    else
      rm -f "$tmp"
      echo "::error::抓取 ruleset $id 失败——已保留原存档不动" >&2
      exit 1
    fi
  done
  # legacy branch protection 也存一份。**注意它只能证明「删之前是什么样」**，
  # 不能一条命令还原：GET 的响应不是 PUT 的请求体格式。
  ltmp="$STORE/.legacy.json.tmp"
  if gh api "repos/$REPO/branches/main/protection" > "$ltmp" 2>/dev/null; then
    mv -f "$ltmp" "$STORE/legacy-branch-protection-$(date +%F).json"
    echo "存档 legacy branch protection（**仅供查阅，不可直接 PUT 还原**）"
  else
    rm -f "$ltmp"
  fi
  ;;

diff)
  rc=0
  # **两个集合都要比。** 只遍历远程 id 的话，某条 ruleset 被**删掉**之后
  # 它压根不出现在循环里 —— `--diff` 于是报绿，而那条规则保护的东西
  # （PR 要求、必需检查、禁止直推 main）已经全部消失。
  # **一个用来检测保护是否完好的工具，在保护完全消失时报平安** ——
  # 这正是这套 CI 反复在消灭的那种失效，而它长在了检测工具自己身上。
  # （Codex 在 #64 第三轮上指出。）
  remote_ids="$(ids)"
  for f in "$STORE"/ruleset-*.json; do
    [ -e "$f" ] || continue
    aid="$(basename "$f" .json | sed 's/^ruleset-//')"
    if ! printf '%s\n' "$remote_ids" | grep -qx "$aid"; then
      name="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name','?'))" "$f")"
      echo "❌ 存档里的 ruleset ${aid}（${name}）在远程**不存在了** —— 它保护的东西现在没人守"
      echo "   重建：${0} --recreate ${aid} --apply"
      rc=1
    fi
  done
  for id in $remote_ids; do
    f="$STORE/ruleset-$id.json"
    if [ ! -f "$f" ]; then echo "⚠️  远程有 ruleset ${id}，本地没有存档"; rc=1; continue; fi
    if gh api "repos/$REPO/rulesets/$id" | norm > /tmp/rs-remote.$$ \
       && norm < "$f" > /tmp/rs-local.$$ \
       && diff -u /tmp/rs-local.$$ /tmp/rs-remote.$$ > /tmp/rs-diff.$$; then
      echo "✅ ruleset $id 与存档一致"
    else
      echo "❌ ruleset $id 与存档不一致（-存档 +远程）："; cat /tmp/rs-diff.$$; rc=1
    fi
    rm -f /tmp/rs-remote.$$ /tmp/rs-local.$$ /tmp/rs-diff.$$
  done
  exit $rc
  ;;

restore)
  # **存档是 GET 的响应，不是 PUT 的请求体。** 里面带着一堆只读字段
  # （`_links` / `id` / `node_id` / `created_at` / `updated_at` /
  # `source` / `source_type` / `current_user_can_bypass`），原样 PUT 回去
  # 会被 API 拒收 —— 而那发生在**最需要它成功的时刻**：某人刚把 ruleset
  # 改坏，正要回滚。
  #
  # 这条与 docs/admin/github-ruleset-changes.md §2.1 自相矛盾过：那份文档
  # 写着「GET 的响应不是 PUT 的请求体」，而这个脚本自己却直接 PUT。
  # Codex 在 #64 的第一轮上指出了这个矛盾。
  for f in "$STORE"/ruleset-*.json; do
    id="$(basename "$f" .json | sed 's/^ruleset-//')"
    tmp="$(mktemp)"
    python3 "$HERE/scripts/admin/_strip_readonly.py" "$f" > "$tmp"
    echo "── ruleset $id ← ${f}（已剥掉只读字段）"
    if [ "$APPLY" = "0" ]; then
      echo "   dry-run。真还原：$0 --restore --apply"
      echo "   将要 PUT 的键：$(python3 -c "import json,sys;print(', '.join(sorted(json.load(open(sys.argv[1])))))" "$tmp")"
    else
      gh api -X PUT "repos/$REPO/rulesets/$id" --input "$tmp" >/dev/null
      echo "   已还原"
    fi
    rm -f "$tmp"
  done
  ;;

recreate)
  # 被删掉的 ruleset **只能 POST 重建**：那个数字 id 已经不存在，
  # `PUT /rulesets/<id>` 会回 404 而不是把它建回来。
  # 重建出来的是一个**新 id**，所以完事必须重新 --backup，
  # 否则下一次 --diff 会说「存档里这条在远程不存在」——而那时它其实在。
  f="$STORE/ruleset-${CHECK}.json"
  [ -f "$f" ] || { echo "找不到存档 $f" >&2; exit 1; }
  if printf '%s\n' "$(ids)" | grep -qx "$CHECK"; then
    echo "ruleset ${CHECK} 在远程还在，用 --restore 而不是 --recreate" >&2; exit 1
  fi
  tmp="$(mktemp)"
  python3 "$HERE/scripts/admin/_strip_readonly.py" "$f" > "$tmp"
  echo "── 重建 ruleset ${CHECK}（POST，会得到一个新 id）"
  if [ "$APPLY" = "0" ]; then
    echo "   dry-run。真重建：${0} --recreate ${CHECK} --apply"
  else
    newid="$(gh api -X POST "repos/${REPO}/rulesets" --input "$tmp" --jq .id)"
    echo "   已重建，新 id = ${newid}"
    # **旧存档要一并清掉。** 不清的话 `--diff`（它比两个集合）会永远报
    # 「存档里的 ruleset <旧 id> 在远程不存在」——而那条规则其实已经回来了，
    # 只是换了个 id。一条永远红的检查会被人忽略，那时它真的红了也没人看。
    gh api "repos/${REPO}/rulesets/${newid}" \
      | python3 -c "import json,sys;json.dump(json.load(sys.stdin),open(sys.argv[1],'w'),indent=2,ensure_ascii=False,sort_keys=True)" \
      "${STORE}/ruleset-${newid}.json"
    rm -f "$f"
    echo "   存档已换成 ruleset-${newid}.json（旧的 ruleset-${CHECK}.json 已删）"
  fi
  rm -f "$tmp"
  ;;

add-check|rm-check)
  # 只动 target=branch 且带 required_status_checks 的那一条
  id="$(gh api "repos/$REPO/rulesets" --jq \
      '.[] | select(.target=="branch") | .id' | head -1)"
  [ -n "$id" ] || { echo "找不到 branch ruleset" >&2; exit 1; }
  cur="$(gh api "repos/$REPO/rulesets/$id")"

  if [ "$MODE" = "add-check" ]; then
    # **登记顺序核对**：这个 check 在 main 上产出过结论吗？
    seen="$(gh api "repos/$REPO/commits/main/check-runs" \
              --jq "[.check_runs[] | select(.name==\"$CHECK\")] | length" 2>/dev/null || echo 0)"
    if [ "$seen" = "0" ]; then
      echo "❌ main 的最新 commit 上没有名为 \"$CHECK\" 的 check run。"
      echo "   必需检查只能登记 main 上**已经产出过结论**的 job——反过来会把"
      echo "   整个仓库锁死（合进来之前那个 check 永远不出现，而规则要求它通过）。"
      echo "   先把产出它的 workflow 合进 main，等它绿过一次再来。"
      [ "$FORCE" = "1" ] || exit 1
      echo "   （--force：明知故犯，继续）"
    else
      echo "✅ main 上见过 \"$CHECK\" 的结论，可以登记"
    fi
  fi

  # **要 export**：内嵌的 python 读的是环境变量，普通 shell 变量它看不见。
  export MODE CHECK
  new="$(printf '%s' "$cur" | python3 -c '
import json,sys,os
mode,check=os.environ["MODE"],os.environ["CHECK"]
d=json.load(sys.stdin)
for k in ("_links","node_id","created_at","updated_at","current_user_can_bypass",
          "id","source","source_type"):
    d.pop(k,None)
for r in d.get("rules",[]):
    if r.get("type")=="required_status_checks":
        lst=r["parameters"]["required_status_checks"]
        names=[c["context"] for c in lst]
        if mode=="add-check":
            if check in names: print("已经在里面了，无事可做",file=sys.stderr)
            else: lst.append({"context":check})
        else:
            r["parameters"]["required_status_checks"]=[c for c in lst if c["context"]!=check]
        break
else:
    print("这条 ruleset 没有 required_status_checks 规则",file=sys.stderr); sys.exit(1)
json.dump(d,sys.stdout,indent=2,ensure_ascii=False,sort_keys=True)')"

  echo "── 变更预览（ruleset $id 的必需检查）"
  printf '%s' "$cur" | python3 -c '
import json,sys
for r in json.load(sys.stdin)["rules"]:
    if r["type"]=="required_status_checks":
        for c in r["parameters"]["required_status_checks"]: print("  当前:",c["context"])'
  printf '%s' "$new" | python3 -c '
import json,sys
for r in json.load(sys.stdin)["rules"]:
    if r["type"]=="required_status_checks":
        for c in r["parameters"]["required_status_checks"]: print("  之后:",c["context"])'

  if [ "$APPLY" = "0" ]; then
    echo "dry-run。真改：加 --apply"
    echo "先跑一次 $0 --backup"
  else
    [ -f "$STORE/ruleset-$id.json" ] || { echo "还没有存档，先 --backup" >&2; exit 1; }
    printf '%s' "$new" | gh api -X PUT "repos/$REPO/rulesets/$id" --input - >/dev/null
    echo "已应用。回滚：$0 --restore --apply"
  fi
  ;;
esac
