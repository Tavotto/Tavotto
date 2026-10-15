//! 退出路径的结构看护（ADR 0129）：**绕过 `RunEvent::Exit` 的退出路径必须先写 `clean`**。
//!
//! 不按子串、不按文件名单：运行时遍历 `src/` 下全部生产 `.rs`（递归；跳过 `*_tests.rs` 与
//! `#[cfg(test)]` 的项 / 模块），用 `syn` 解析成 AST。**不做 `use` 别名 / 作用域解析**
//! （同一文件里不同作用域可以把同一个名字绑到不同东西，解析永远有漏）——改从结构上消掉问题面：
//!
//! - `use` 树里出现 `std` / `core` 的 `process` 段、或导入 `exit` / `restart` / `updater_builder` 的，
//!   一律算一次 hit（`use process`，每条 `use` 至多一次）；别名、glob、`self as p` 都过不了这一关；
//! - 任何末段是 `exit` 的路径（不论前缀）、末段是 `restart` 的路径、带前缀的 `updater_builder` 路径，
//!   以及 `.exit()` / `.restart()` / `.updater_builder()` 方法调用，都算 hit；
//! - 宏体 syn 不解析：遍历 TokenTree，跳过 Literal、递归进 Group，只看 Ident。
//!
//! 只数 AST 节点，注释与字符串字面量不会误报。允许名单只列确实存在的那几处，且每一项都必须真的命中。

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use proc_macro2::{TokenStream, TokenTree};
use syn::visit::Visit;

/// (文件相对 src 的路径, 外层函数名, 出口种类)
pub type Hit = (String, String, &'static str);

#[derive(Default)]
struct Scan {
    file: String,
    fns: Vec<String>,
    hits: Vec<Hit>,
}

fn is_cfg_test(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| {
        a.path().is_ident("cfg") && {
            let mut test = false;
            let _ = a.parse_nested_meta(|m| {
                if m.path.is_ident("test") {
                    test = true;
                }
                Ok(())
            });
            test
        }
    })
}

/// 这条 `use` 树里有没有 `std` / `core` 的 `process` 段，或导入了 `exit` / `restart` / `updater_builder`。
fn use_is_suspect(tree: &syn::UseTree, path: &mut Vec<String>) -> bool {
    let leaf_check = |path: &[String], leaf: &str| {
        matches!(leaf, "exit" | "restart" | "updater_builder")
            || (matches!(path.first().map(String::as_str), Some("std" | "core"))
                && (path.iter().any(|s| s == "process") || leaf == "process"))
    };
    match tree {
        syn::UseTree::Path(p) => {
            path.push(p.ident.to_string());
            let r = use_is_suspect(&p.tree, path);
            path.pop();
            r
        }
        syn::UseTree::Name(n) => leaf_check(path, &n.ident.to_string()),
        syn::UseTree::Rename(r) => leaf_check(path, &r.ident.to_string()),
        syn::UseTree::Glob(_) => leaf_check(path, "*"),
        syn::UseTree::Group(g) => g.items.iter().any(|t| use_is_suspect(t, path)),
    }
}

impl Scan {
    fn hit(&mut self, kind: &'static str) {
        let f = self.fns.last().cloned().unwrap_or_default();
        self.hits.push((self.file.clone(), f, kind));
    }

    /// 宏体 syn 不解析：遍历 TokenTree，跳过字面量，递归进分组，只看标识符。
    fn scan_tokens(&mut self, ts: TokenStream) {
        for tt in ts {
            match tt {
                TokenTree::Group(g) => self.scan_tokens(g.stream()),
                TokenTree::Ident(i) => match i.to_string().as_str() {
                    "exit" => self.hit("process::exit"),
                    "restart" => self.hit("restart"),
                    "updater_builder" => self.hit("updater_builder"),
                    _ => {}
                },
                TokenTree::Literal(_) | TokenTree::Punct(_) => {}
            }
        }
    }
}

impl<'ast> Visit<'ast> for Scan {
    fn visit_item(&mut self, i: &'ast syn::Item) {
        use syn::Item::*;
        let attrs: &[syn::Attribute] = match i {
            Fn(x) => &x.attrs,
            Mod(x) => &x.attrs,
            Impl(x) => &x.attrs,
            Const(x) => &x.attrs,
            Static(x) => &x.attrs,
            Use(x) => &x.attrs,
            Trait(x) => &x.attrs,
            _ => &[],
        };
        if is_cfg_test(attrs) {
            return;
        }
        syn::visit::visit_item(self, i);
    }

    fn visit_item_use(&mut self, u: &'ast syn::ItemUse) {
        if use_is_suspect(&u.tree, &mut Vec::new()) {
            self.hit("use process");
        }
    }

    fn visit_item_fn(&mut self, f: &'ast syn::ItemFn) {
        self.fns.push(f.sig.ident.to_string());
        syn::visit::visit_item_fn(self, f);
        self.fns.pop();
    }

    fn visit_impl_item_fn(&mut self, f: &'ast syn::ImplItemFn) {
        if is_cfg_test(&f.attrs) {
            return;
        }
        self.fns.push(f.sig.ident.to_string());
        syn::visit::visit_impl_item_fn(self, f);
        self.fns.pop();
    }

    fn visit_expr_path(&mut self, p: &'ast syn::ExprPath) {
        let n = p.path.segments.len();
        match p.path.segments[n - 1].ident.to_string().as_str() {
            "exit" => self.hit("process::exit"),
            "restart" => self.hit("restart"),
            // 单段 `updater_builder(..)` 是 main.rs 自己的助手；带前缀的（`UpdaterExt::updater_builder(&a)`）才是绕过
            "updater_builder" if n >= 2 => self.hit("updater_builder"),
            _ => {}
        }
        syn::visit::visit_expr_path(self, p);
    }

    fn visit_expr_method_call(&mut self, m: &'ast syn::ExprMethodCall) {
        match m.method.to_string().as_str() {
            "restart" => self.hit("restart"),
            "exit" => self.hit("process::exit"),
            "updater_builder" => self.hit("updater_builder"),
            _ => {}
        }
        syn::visit::visit_expr_method_call(self, m);
    }

    fn visit_macro(&mut self, m: &'ast syn::Macro) {
        self.scan_tokens(m.tokens.clone());
        syn::visit::visit_macro(self, m);
    }
}

/// 扫一段源码（单测直接喂片段）。
pub fn scan_source(file: &str, src: &str) -> Vec<Hit> {
    let ast = syn::parse_file(src).unwrap_or_else(|e| panic!("{file} 解析失败: {e}"));
    let mut scan = Scan {
        file: file.to_string(),
        ..Scan::default()
    };
    scan.visit_file(&ast);
    scan.hits
}

fn rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .unwrap_or_else(|e| panic!("读不了 {}: {e}", dir.display()))
        .map(|e| e.unwrap().path())
        .collect();
    entries.sort();
    for p in entries {
        if p.is_dir() {
            rs_files(&p, out);
        } else if p.extension().is_some_and(|e| e == "rs") {
            out.push(p);
        }
    }
}

/// 全部生产 `.rs` 里的出口命中：跳过 `*_tests.rs`，以及被 `#[cfg(test)] mod x;` 声明的文件模块。
pub fn production_hits() -> Vec<Hit> {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rs_files(&src, &mut files);
    let mut test_mods: BTreeSet<String> = BTreeSet::new();
    for f in &files {
        let ast = syn::parse_file(&std::fs::read_to_string(f).unwrap()).unwrap();
        for item in &ast.items {
            if let syn::Item::Mod(m) = item {
                if is_cfg_test(&m.attrs) && m.content.is_none() {
                    test_mods.insert(m.ident.to_string());
                }
            }
        }
    }
    let mut hits = Vec::new();
    for f in files {
        let stem = f.file_stem().unwrap().to_string_lossy().into_owned();
        if stem.ends_with("_tests") || test_mods.contains(&stem) {
            continue;
        }
        let rel = f
            .strip_prefix(&src)
            .unwrap()
            .to_string_lossy()
            .replace('\\', "/");
        hits.extend(scan_source(&rel, &std::fs::read_to_string(&f).unwrap()));
    }
    hits
}

#[test]
fn only_the_whitelisted_exit_functions_leave_the_process() {
    let hits = production_hits();
    // 允许名单：先写 clean 的出口函数本身（顺序由 main.rs 的结构测试另行钉住），
    // 以及 sidecar.rs 为 spawn 子进程导入的 `std::process::{Child, ..}`（它不含 exit）
    let allowed: [Hit; 6] = [
        ("main.rs".into(), "e2e_exit".into(), "process::exit"),
        (
            "main.rs".into(),
            "updater_builder".into(),
            "updater_builder",
        ),
        (
            "main.rs".into(),
            "spawn_e2e_update_if_requested".into(),
            "restart",
        ),
        ("sidecar.rs".into(), String::new(), "use process"),
        // Windows 上给 Command 加 CREATE_NO_WINDOW 的 `std::os::windows::process::CommandExt`（不含 exit）
        ("sidecar.rs".into(), "start".into(), "use process"),
        ("main.rs".into(), "codex_integration".into(), "use process"),
    ];
    let rogue: Vec<_> = hits.iter().filter(|h| !allowed.contains(h)).collect();
    assert!(
        rogue.is_empty(),
        "出现了不在允许名单里的退出路径（先 mark_clean 再加看护）: {rogue:?}"
    );
    for a in &allowed {
        assert!(
            hits.contains(a),
            "允许名单里的 {a:?} 不见了：看护失效或出口已改道"
        );
    }
}

/// 每种写法单独一条：同一条里混两种写法，一个被认出就会掩盖另一个漏报。
macro_rules! catches {
    ($name:ident, $kind:expr, $src:expr) => {
        #[test]
        fn $name() {
            let kinds: Vec<&str> = scan_source("x.rs", $src).into_iter().map(|h| h.2).collect();
            assert!(kinds.contains(&$kind), "漏报: {}", $src);
        }
    };
}
macro_rules! ignores {
    ($name:ident, $src:expr) => {
        #[test]
        fn $name() {
            let hits = scan_source("x.rs", $src);
            assert!(hits.is_empty(), "误报: {} -> {hits:?}", $src);
        }
    };
}

catches!(
    full_path,
    "process::exit",
    "fn f() { std::process::exit(1); }"
);
catches!(
    leading_colons,
    "process::exit",
    "fn f() { ::std::process::exit(1) }"
);
catches!(
    use_module,
    "use process",
    "use std::process; fn f() { process::exit(1) }"
);
catches!(
    use_module_then_exit,
    "process::exit",
    "use std::process; fn f() { process::exit(1) }"
);
catches!(
    use_rename_exit,
    "use process",
    "use std::process::exit as quit; fn f() { quit(1) }"
);
catches!(
    use_glob,
    "use process",
    "use std::process::*; fn f() { exit(1) }"
);
catches!(
    use_glob_then_exit,
    "process::exit",
    "use std::process::*; fn f() { exit(1) }"
);
catches!(
    use_std_glob,
    "process::exit",
    "use std::*; fn f() { process::exit(1) }"
);
catches!(
    use_self_as,
    "use process",
    "use std::process::{self as p}; fn f() { p::exit(1) }"
);
catches!(
    use_self,
    "use process",
    "use std::process::{self}; fn f() { process::exit(1) }"
);
catches!(
    use_nested_self_as,
    "use process",
    "use std::{process::{self as p}}; fn f() { p::exit(1) }"
);
catches!(
    use_group_exit,
    "use process",
    "use std::process::{exit as q}; fn f() { q(2) }"
);
catches!(use_core, "use process", "use core::process; fn f() {}");
catches!(
    use_in_fn_body,
    "use process",
    "fn f() { use std::process as p; p::exit(1) }"
);
catches!(
    fn_pointer,
    "process::exit",
    "fn f() { let g = std::process::exit; g(1) }"
);
catches!(
    odd_whitespace,
    "process::exit",
    "fn f() { std :: process\n  :: exit ( 1 ) }"
);
catches!(any_prefix, "process::exit", "fn f() { whatever::exit(1) }");
catches!(method_exit, "process::exit", "fn f(a: A) { a.exit(0) }");
catches!(
    call_inside_macro_args,
    "process::exit",
    "fn f() { println!(\"{}\", std::process::exit(1)) }"
);
catches!(method_restart, "restart", "fn f(h: H) { h.restart() }");
catches!(
    use_restart,
    "use process",
    "use tauri::process::restart; fn f() { restart(&e) }"
);
catches!(
    use_restart_then_call,
    "restart",
    "use tauri::process::restart; fn f() { restart(&e) }"
);
catches!(
    path_restart,
    "restart",
    "fn f() { tauri::process::restart(&e) }"
);
catches!(
    method_updater_builder,
    "updater_builder",
    "fn f(a: A) { a.updater_builder() }"
);
catches!(
    ufcs_updater_builder,
    "updater_builder",
    "fn f(a: A) { UpdaterExt::updater_builder(&a) }"
);
// Codex #1070 r4279998543：同一文件两个作用域里同名别名 `p` 绑到不同东西——按文件收别名会漏判
catches!(
    same_alias_two_scopes_exit_second,
    "process::exit",
    "mod a { use other::p; pub fn f() { p::go(); } } mod b { use std::process as p; pub fn g() { p::exit(1) } }"
);
catches!(
    same_alias_two_scopes_use_second,
    "use process",
    "mod a { use other::p; pub fn f() { p::go(); } } mod b { use std::process as p; pub fn g() { p::exit(1) } }"
);
catches!(
    same_alias_two_scopes_exit_first,
    "process::exit",
    "mod a { use std::process as p; pub fn f() { p::exit(1) } } mod b { use other::p; pub fn g() { p::go(); } }"
);

ignores!(
    comment_and_string,
    "// std::process::exit(1)\nfn f() { let _ = \"std::process::exit(1)\"; }"
);
ignores!(local_helper, "fn f() { updater_builder(&a) }");
ignores!(
    cfg_test_module,
    "#[cfg(test)] mod tests { fn f() { std::process::exit(1) } }"
);
ignores!(cfg_test_fn, "#[cfg(test)] fn f() { std::process::exit(1) }");
ignores!(cfg_test_use, "#[cfg(test)] use std::process::exit;");
ignores!(
    unrelated_use,
    "use std::collections::HashMap; use tokio::process::Command;"
);
// Codex #1070 r4279998546：宏里的字符串字面量不是调用
ignores!(
    macro_string_restart,
    "fn f() { tracing::info!(\"restart failed\"); }"
);
ignores!(macro_string_exit, "fn f() { println!(\"exit\"); }");
