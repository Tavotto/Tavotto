//! patch 规范化与内容寻址哈希——**`src/tavotto/engine/patchspec.py` 的 Rust 复刻**。
//!
//! 权威实现在 Python 那边；这里的唯一职责是**逐字节复现它的输出**。
//! 硬验收是 `tests/golden/patch_vectors.json`（仓库根，两侧共用同一份），
//! `tests/golden_vectors.rs` 逐组断言 canonical / dropped / canonical_json / hash。
//! 契约与已知的跨语言坑见 `docs/adr/0003-worker-protocol-v1.md`。
//!
//! **规范序只决定身份，不决定应用顺序**：发给 worker 的永远是请求里那份原始
//! 列表，这里排过序的那份只用来算哈希。

use std::collections::BTreeMap;

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::pyfloat::format_f64;

/// 嵌套深度上限，与 Python 的 `_MAX_DEPTH` 同值。
const MAX_DEPTH: usize = 32;

/// 被剔除的条目：索引 + 机器可读的原因。
///
/// `index == -1` 表示整个 patches 根本不是列表。原因串**照抄 Python**——
/// 上层（前端的「有条 patch 没生效」提示）认的就是这些字符串。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dropped {
    pub index: i64,
    pub reason: String,
}

impl Dropped {
    fn new(index: i64, reason: &str) -> Self {
        Self {
            index,
            reason: reason.to_string(),
        }
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({"index": self.index, "reason": self.reason})
    }
}

/// 规范化结果的一条：键序固定 gid < prop < value。
#[derive(Debug, Clone, PartialEq)]
pub struct CanonEntry {
    pub gid: String,
    pub prop: String,
    pub value: Value,
}

/// 规范化并同时交出被剔除的条目（对应 `canonicalize_with_diagnostics`）。
///
/// 三条规则与 Python 一字不差：形状校验 → 同 (gid, prop) last-wins →
/// 按 (gid, prop) 字典序排。**剔除永远是可见的**，静默丢一条 patch，
/// 用户看到的是「我改了但没生效」而没有任何线索。
pub fn canonicalize_with_diagnostics(patches: &Value) -> (Vec<CanonEntry>, Vec<Dropped>) {
    let items = match patches.as_array() {
        Some(items) => items,
        None => return (Vec::new(), vec![Dropped::new(-1, "patches_not_a_list")]),
    };

    let mut dropped = Vec::new();
    // BTreeMap 同时办了 last-wins（insert 覆盖）与排序（迭代即有序）。
    // Rust 的 String Ord 是 UTF-8 字节序，与 Python 的码点序在合法字符串上等价。
    let mut merged: BTreeMap<(String, String), Value> = BTreeMap::new();

    for (i, entry) in items.iter().enumerate() {
        let idx = i as i64;
        let obj = match entry.as_object() {
            Some(obj) => obj,
            None => {
                dropped.push(Dropped::new(idx, "not_an_object"));
                continue;
            }
        };
        let gid = match obj.get("gid").and_then(Value::as_str) {
            Some(gid) if !gid.is_empty() => gid,
            _ => {
                dropped.push(Dropped::new(idx, "bad_gid"));
                continue;
            }
        };
        let prop = match obj.get("prop").and_then(Value::as_str) {
            Some(prop) if !prop.is_empty() => prop,
            _ => {
                dropped.push(Dropped::new(idx, "bad_prop"));
                continue;
            }
        };
        let value = match obj.get("value") {
            Some(value) => value,
            None => {
                dropped.push(Dropped::new(idx, "missing_value"));
                continue;
            }
        };
        if let Some(problem) = value_problem(value, 0) {
            dropped.push(Dropped::new(idx, problem));
            continue;
        }
        merged.insert((gid.to_string(), prop.to_string()), value.clone());
    }

    let canonical = merged
        .into_iter()
        .map(|((gid, prop), value)| CanonEntry { gid, prop, value })
        .collect();
    (canonical, dropped)
}

/// 只要规范化结果时用这个。
pub fn canonicalize(patches: &Value) -> Vec<CanonEntry> {
    canonicalize_with_diagnostics(patches).0
}

/// 确定性序列化——`json.dumps(..., sort_keys=True, separators=(",",":"),
/// ensure_ascii=False, allow_nan=False)` 的逐字节等价物。
pub fn canonical_json(patches: &Value) -> String {
    let canonical = canonicalize(patches);
    let mut out = String::from("[");
    for (i, entry) in canonical.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        // 键序 gid < prop < value 正是 sort_keys 的结果，写死即可。
        out.push_str("{\"gid\":");
        write_string(&entry.gid, &mut out);
        out.push_str(",\"prop\":");
        write_string(&entry.prop, &mut out);
        out.push_str(",\"value\":");
        write_value(&entry.value, &mut out);
        out.push('}');
    }
    out.push(']');
    out
}

/// `"sha256:" + sha256(canonical_json.encode("utf-8")).hexdigest()`。
pub fn patch_hash(patches: &Value) -> String {
    sha256_hex(canonical_json(patches).as_bytes())
}

/// `"sha256:"` 前缀的十六进制摘要（spawn 规格的键也用它）。
pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    let digest = hasher.finalize();
    let mut out = String::with_capacity(7 + digest.len() * 2);
    out.push_str("sha256:");
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

/// value 不是合法 JSON 值时回一句机器可读的原因（对应 `_value_problem`）。
fn value_problem(value: &Value, depth: usize) -> Option<&'static str> {
    if depth > MAX_DEPTH {
        return Some("value_too_deep");
    }
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => None,
        Value::Number(_) => match classify_number(value) {
            Number::Int(_) => None,
            Number::Float(f) if f.is_finite() => None,
            Number::Float(_) => Some("non_finite_float"),
            Number::IntOutOfRange => Some("int_out_of_range"),
        },
        Value::Array(items) => items.iter().find_map(|item| value_problem(item, depth + 1)),
        // JSON 的对象键必然是字符串，`non_string_object_key` 在这条路上走不到
        // （Python 那边能走到是因为 dict 的键可以是任何可哈希对象）。
        Value::Object(map) => map.values().find_map(|item| value_problem(item, depth + 1)),
    }
}

enum Number {
    Int(i64),
    IntOutOfRange,
    Float(f64),
}

/// 整数还是浮点，**按字面量判**而不是按能不能塞进 i64。
///
/// Python 的 `json` 用字面量里有没有 `.`/`e` 决定给 int 还是 float：
/// `1` 是整数、`1.0` 与 `1e5` 是浮点，序列化时写法完全不同。序号靠
/// serde_json 的 arbitrary_precision 保住原始字面量才能照办；没有它，
/// 超出 u64 的整数会被悄悄变成 f64，与 Python 的 `int_out_of_range` 分叉。
fn classify_number(value: &Value) -> Number {
    let literal = match value {
        Value::Number(n) => n.as_str(),
        _ => return Number::Float(f64::NAN),
    };
    let is_integer_literal = {
        let body = literal.strip_prefix('-').unwrap_or(literal);
        !body.is_empty() && body.bytes().all(|b| b.is_ascii_digit())
    };
    if is_integer_literal {
        return match literal.parse::<i64>() {
            Ok(v) => Number::Int(v),
            Err(_) => Number::IntOutOfRange,
        };
    }
    Number::Float(literal.parse::<f64>().unwrap_or(f64::NAN))
}

/// 任意 JSON 值 → Python `json.dumps(..., sort_keys=True, ensure_ascii=False)`
/// 的等价文本（嵌套对象的键同样递归排序）。
pub fn write_value(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(_) => match classify_number(value) {
            Number::Int(v) => out.push_str(&v.to_string()),
            Number::Float(f) => out.push_str(&format_f64(f)),
            // 规范化已经把它剔除了；真走到这里也不许写出非法 JSON
            Number::IntOutOfRange => out.push_str("null"),
        },
        Value::String(s) => write_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_value(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort(); // sort_keys=True 是**递归**的
            out.push('{');
            for (i, key) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_string(key, out);
                out.push(':');
                write_value(&map[key.as_str()], out);
            }
            out.push('}');
        }
    }
}

/// 字符串转义——照抄 CPython `json.encoder` 的 ESCAPE_DCT（ensure_ascii=False）。
///
/// 只转义 `"`、`\` 与 C0 控制字符；`/`、U+2028/2029、DEL 一律原样出字。
/// 多转一个字符就是一次哈希分叉，这里不许「顺手更安全一点」。
fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn parse(text: &str) -> Value {
        serde_json::from_str(text).unwrap()
    }

    #[test]
    fn empty_list_has_a_stable_identity() {
        assert_eq!(canonical_json(&json!([])), "[]");
        assert_eq!(
            patch_hash(&json!([])),
            "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
        );
    }

    #[test]
    fn not_a_list_is_reported_at_index_minus_one() {
        let (canonical, dropped) = canonicalize_with_diagnostics(&json!({"gid": "a"}));
        assert!(canonical.is_empty());
        assert_eq!(dropped, vec![Dropped::new(-1, "patches_not_a_list")]);
    }

    #[test]
    fn integer_literals_beyond_u64_are_out_of_range_not_floats() {
        // 没有 arbitrary_precision 的话这条会被解析成 f64 并**照单全收**，
        // 与 Python 的 int_out_of_range 分叉。
        let patches = parse(r#"[{"gid":"g","prop":"p","value":1000000000000000000000000000000}]"#);
        let (canonical, dropped) = canonicalize_with_diagnostics(&patches);
        assert!(canonical.is_empty());
        assert_eq!(dropped, vec![Dropped::new(0, "int_out_of_range")]);
    }

    #[test]
    fn int_and_float_one_are_two_different_values() {
        let patches =
            parse(r#"[{"gid":"g","prop":"i","value":1},{"gid":"g","prop":"f","value":1.0}]"#);
        assert_eq!(
            canonical_json(&patches),
            r#"[{"gid":"g","prop":"f","value":1.0},{"gid":"g","prop":"i","value":1}]"#
        );
    }

    #[test]
    fn nested_object_keys_are_sorted_recursively() {
        let patches = parse(r#"[{"gid":"g","prop":"p","value":{"b":1,"a":{"z":2,"y":3}}}]"#);
        assert_eq!(
            canonical_json(&patches),
            r#"[{"gid":"g","prop":"p","value":{"a":{"y":3,"z":2},"b":1}}]"#
        );
    }

    #[test]
    fn depth_limit_is_thirty_two() {
        // 33 层嵌套数组：Python 的 _MAX_DEPTH 判据是 depth > 32
        let mut deep = String::new();
        for _ in 0..33 {
            deep.push('[');
        }
        deep.push('1');
        for _ in 0..33 {
            deep.push(']');
        }
        let patches = parse(&format!(r#"[{{"gid":"g","prop":"p","value":{deep}}}]"#));
        let (_, dropped) = canonicalize_with_diagnostics(&patches);
        assert_eq!(dropped, vec![Dropped::new(0, "value_too_deep")]);

        let mut ok = String::new();
        for _ in 0..32 {
            ok.push('[');
        }
        ok.push('1');
        for _ in 0..32 {
            ok.push(']');
        }
        let patches = parse(&format!(r#"[{{"gid":"g","prop":"p","value":{ok}}}]"#));
        assert!(canonicalize_with_diagnostics(&patches).1.is_empty());
    }

    #[test]
    fn control_characters_use_the_python_escape_table() {
        // 值里依次是: a, U+0001, b, TAB, c, DEL, '/', U+2028
        let raw = "[{\"gid\":\"g\",\"prop\":\"p\",\"value\":\"a\\u0001b\\tc\\u007f/\\u2028\"}]";
        let patches = parse(raw);
        // \t 与 U+0001 走 Python 的转义表；DEL、'/'、U+2028 一律**原样出字**
        // （ensure_ascii=False 下 Python 不碰它们，多转一个就是一次哈希分叉）
        let expected = format!(
            "[{{\"gid\":\"g\",\"prop\":\"p\",\"value\":\"a\\u0001b\\tc{}/{}\"}}]",
            '\u{7f}', '\u{2028}'
        );
        assert_eq!(canonical_json(&patches), expected);
    }
}
