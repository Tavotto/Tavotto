//! **硬验收**：Rust 的 patch 规范化必须逐字节复现 `patchspec.py`。
//!
//! 向量文件 `tests/golden/patch_vectors.json` 在仓库根，Python 侧
//! （`tests/test_patchspec.py`）读的是同一份。**这不是一组示例，是契约**：
//! 两边算出来的 `canonical_patch_hash` 一旦分叉，worker 会在每次渲染时报
//! `hash_mismatch`，而「同一份修改」的缓存与幂等重放全部失效。

use std::path::PathBuf;

use magplot_workerd::patchspec::{
    canonical_json, canonicalize_with_diagnostics, patch_hash, write_value,
};
use serde_json::Value;

fn vectors_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("tests")
        .join("golden")
        .join("patch_vectors.json")
}

fn render(value: &Value) -> String {
    let mut out = String::new();
    write_value(value, &mut out);
    out
}

#[test]
fn every_golden_vector_matches_byte_for_byte() {
    let path = vectors_path();
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("读不到 golden vectors {}: {e}", path.display()));
    let doc: Value = serde_json::from_str(&text).expect("golden vectors 不是合法 JSON");
    let vectors = doc["vectors"].as_array().expect("vectors 必须是数组");
    assert!(
        vectors.len() >= 11,
        "向量少了（现在 {}），是不是漏同步了 Python 侧？",
        vectors.len()
    );

    for vector in vectors {
        let name = vector["name"].as_str().unwrap_or("<无名>");
        let input = &vector["input"];
        let (canonical, dropped) = canonicalize_with_diagnostics(input);

        // 1) canonical：条目内容逐条比（value 走同一个序列化器，避免拿
        //    serde_json 的 Number 相等去比 1 与 1.0 这种致命的等价）
        let expected = vector["canonical"]
            .as_array()
            .expect("canonical 必须是数组");
        assert_eq!(
            canonical.len(),
            expected.len(),
            "[{name}] canonical 条数不对: {canonical:?}"
        );
        for (got, want) in canonical.iter().zip(expected) {
            assert_eq!(got.gid, want["gid"].as_str().unwrap(), "[{name}] gid");
            assert_eq!(got.prop, want["prop"].as_str().unwrap(), "[{name}] prop");
            assert_eq!(
                render(&got.value),
                render(&want["value"]),
                "[{name}] {}::{} 的 value",
                got.gid,
                got.prop
            );
        }

        // 2) dropped：索引与原因串照抄 Python，一个字都不许改
        let want_dropped = vector["dropped"].as_array().expect("dropped 必须是数组");
        let got_dropped: Vec<(i64, &str)> = dropped
            .iter()
            .map(|d| (d.index, d.reason.as_str()))
            .collect();
        let want_pairs: Vec<(i64, &str)> = want_dropped
            .iter()
            .map(|d| (d["index"].as_i64().unwrap(), d["reason"].as_str().unwrap()))
            .collect();
        assert_eq!(got_dropped, want_pairs, "[{name}] dropped");

        // 3) canonical_json：逐字节
        let want_json = vector["canonical_json"]
            .as_str()
            .expect("canonical_json 必须是字符串");
        assert_eq!(canonical_json(input), want_json, "[{name}] canonical_json");

        // 4) hash
        let want_hash = vector["hash"].as_str().expect("hash 必须是字符串");
        assert_eq!(patch_hash(input), want_hash, "[{name}] hash");
    }
}

#[test]
fn equivalent_writings_share_one_hash() {
    // 向量文件里 order_independent / sorted_equivalent 就是同一份修改的两种写法。
    let text = std::fs::read_to_string(vectors_path()).unwrap();
    let doc: Value = serde_json::from_str(&text).unwrap();
    let mut by_name = std::collections::BTreeMap::new();
    for vector in doc["vectors"].as_array().unwrap() {
        by_name.insert(vector["name"].as_str().unwrap().to_string(), vector.clone());
    }
    let a = patch_hash(&by_name["order_independent"]["input"]);
    let b = patch_hash(&by_name["sorted_equivalent"]["input"]);
    assert_eq!(a, b, "乱序 + 重复的写法必须与排好序的写法同 hash");
}

#[test]
fn canonicalization_is_idempotent() {
    // 规范化结果再规范化一次必须原样不动——否则「这两次渲染是不是同一件事」
    // 会依赖调用次数。
    let text = std::fs::read_to_string(vectors_path()).unwrap();
    let doc: Value = serde_json::from_str(&text).unwrap();
    for vector in doc["vectors"].as_array().unwrap() {
        let name = vector["name"].as_str().unwrap();
        let once = canonical_json(&vector["input"]);
        let reparsed: Value = serde_json::from_str(&once).unwrap();
        assert_eq!(canonical_json(&reparsed), once, "[{name}] 规范化不幂等");
    }
}
