//! 退出路径的结构看护（ADR 0129）：**绕过 `RunEvent::Exit` 的退出路径必须先写 `clean`**。
//!
//! 不按子串、不按文件名单：运行时遍历 `src/` 下全部生产 `.rs`（递归；跳过 `*_tests.rs` 与
//! `#[cfg(test)]` 的项 / 模块），用 `syn` 解析成 AST。**不做 `use` 别名 / 作用域解析**
//! （同一文件里不同作用域可以把同一个名字绑到不同东西，解析永远有漏）——改从结构上消掉问题面：
//!
//! - `use` 树按**叶子**逐个判：路径在 `std` / `core` 下含 `process` 段、或导入 `exit` / `restart` / `updater_builder` 的叶子，
//!   各算一次 hit，写法带原名与别名（如 `use std::process::exit as quit`）；别名、glob、`self as p` 都过不了这一关；
//! - 任何末段是 `exit` 的路径（不论前缀）、末段是 `restart` 的路径、带前缀的 `updater_builder` 路径，
//!   以及 `.exit()` / `.restart()` / `.updater_builder()` 方法调用，都算 hit；
//! - 宏体 syn 不解析：遍历 TokenTree，跳过 Literal、递归进 Group，只看 Ident。
//!
//! 只数 AST 节点，注释与字符串字面量不会误报。允许名单列到具体叶子，与实际命中按**多重集**精确相等：多一项、少一项、计数不同都失败。

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use proc_macro2::{TokenStream, TokenTree};
use syn::visit::Visit;

/// (文件相对 src 的路径, 外层函数名, 出口种类)
pub type Hit = (String, String, String);

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

/// 把 `use` 树展开成叶子：(完整路径含叶子, 展示写法)。`self` 叶子的路径就是父路径加 `self`。
fn use_leaves(tree: &syn::UseTree, path: &mut Vec<String>, out: &mut Vec<(Vec<String>, String)>) {
    let show = |full: &[String], rename: Option<String>| {
        let base = full.join("::");
        match rename {
            Some(r) => format!("use {base} as {r}"),
            None => format!("use {base}"),
        }
    };
    match tree {
        syn::UseTree::Path(p) => {
            path.push(p.ident.to_string());
            use_leaves(&p.tree, path, out);
            path.pop();
        }
        syn::UseTree::Name(n) => {
            let mut full = path.clone();
            full.push(n.ident.to_string());
            let d = show(&full, None);
            out.push((full, d));
        }
        syn::UseTree::Rename(r) => {
            let mut full = path.clone();
            full.push(r.ident.to_string());
            let d = show(&full, Some(r.rename.to_string()));
            out.push((full, d));
        }
        syn::UseTree::Glob(_) => {
            let mut full = path.clone();
            full.push("*".into());
            let d = show(&full, None);
            out.push((full, d));
        }
        syn::UseTree::Group(g) => {
            for t in &g.items {
                use_leaves(t, path, out);
            }
        }
    }
}

/// 叶子可疑：路径在 `std` / `core` 下且含 `process` 段，或导入的原名是 `exit` / `restart` / `updater_builder`。
fn leaf_is_suspect(full: &[String]) -> bool {
    matches!(
        full.last().map(String::as_str),
        Some("exit" | "restart" | "updater_builder")
    ) || (matches!(full.first().map(String::as_str), Some("std" | "core"))
        && full.iter().any(|s| s == "process"))
}

impl Scan {
    fn hit(&mut self, kind: &str) {
        let f = self.fns.last().cloned().unwrap_or_default();
        self.hits.push((self.file.clone(), f, kind.to_string()));
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
        // 逐叶子产生 hit，带叶子的原名与别名：允许名单精确到叶子，往放行的组里再塞一个 `exit as quit` 会多出一项
        let mut leaves = Vec::new();
        use_leaves(&u.tree, &mut Vec::new(), &mut leaves);
        for (full, shown) in leaves {
            if leaf_is_suspect(&full) {
                self.hit(&shown);
            }
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

/// 实际命中与允许名单的多重集差：(多出来的, 缺失的)。
fn multiset_diff(actual: &[Hit], allowed: &[Hit]) -> (Vec<Hit>, Vec<Hit>) {
    let mut rest: Vec<Hit> = allowed.to_vec();
    let mut extra = Vec::new();
    for h in actual {
        match rest.iter().position(|a| a == h) {
            Some(i) => {
                rest.swap_remove(i);
            }
            None => extra.push(h.clone()),
        }
    }
    (extra, rest)
}

fn allowed_hits() -> Vec<Hit> {
    let h = |f: &str, func: &str, k: &str| -> Hit { (f.into(), func.into(), k.into()) };
    vec![
        // 先写 clean 的出口函数本身（顺序由 main.rs 的结构测试另行钉住）
        h("main.rs", "e2e_exit", "process::exit"),
        h("main.rs", "updater_builder", "updater_builder"),
        h("main.rs", "spawn_e2e_update_if_requested", "restart"),
        // sidecar.rs 为 spawn 子进程导入的类型（不含 exit）
        h("sidecar.rs", "", "use std::process::Child"),
        h("sidecar.rs", "", "use std::process::ChildStdin"),
        h("sidecar.rs", "", "use std::process::Command"),
        h("sidecar.rs", "", "use std::process::Stdio"),
        // Windows 上给 Command 加 CREATE_NO_WINDOW
        h(
            "sidecar.rs",
            "start",
            "use std::os::windows::process::CommandExt",
        ),
        h(
            "main.rs",
            "codex_integration",
            "use std::os::windows::process::CommandExt",
        ),
    ]
}

#[test]
fn production_exits_equal_the_whitelist_exactly() {
    let (extra, missing) = multiset_diff(&production_hits(), &allowed_hits());
    assert!(
        extra.is_empty(),
        "出现了不在允许名单里的退出路径（先 mark_clean 再加看护）: {extra:?}"
    );
    assert!(
        missing.is_empty(),
        "允许名单里的项没有命中（看护失效或出口已改道）: {missing:?}"
    );
}

/// Codex #1070 r4280114950 之前一轮的样例：往已放行的 `std::process::{Child, ..}` 组里再加 `exit as quit` 并调用，
/// 旧的 (文件, 函数, 类型) 粒度不变、测试仍绿；叶子粒度 + 多重集必须红。
#[test]
fn adding_exit_to_an_allowed_use_group_is_caught() {
    let sneaky = "use std::process::{Child, ChildStdin, Command, Stdio, exit as quit};\n\
                  fn start() { use std::os::windows::process::CommandExt; quit(1) }";
    let hits = scan_source("sidecar.rs", sneaky);
    let sidecar_allowed: Vec<Hit> = allowed_hits()
        .into_iter()
        .filter(|h| h.0 == "sidecar.rs")
        .collect();
    let (extra, _) = multiset_diff(&hits, &sidecar_allowed);
    assert!(
        extra
            .iter()
            .any(|h| h.2 == "use std::process::exit as quit"),
        "漏报: {extra:?}"
    );
    // 同一叶子重复出现（计数不同）也不行
    let doubled = [sidecar_allowed.clone(), vec![sidecar_allowed[0].clone()]].concat();
    let (_, missing) = multiset_diff(&sidecar_allowed, &doubled);
    assert_eq!(missing.len(), 1);
}

/// 每种写法单独一条：同一条里混两种写法，一个被认出就会掩盖另一个漏报。
macro_rules! catches {
    ($name:ident, $kind:expr, $src:expr) => {
        #[test]
        fn $name() {
            let kinds: Vec<String> = scan_source("x.rs", $src).into_iter().map(|h| h.2).collect();
            assert!(
                kinds.iter().any(|k| k == $kind),
                "漏报: {} -> {kinds:?}",
                $src
            );
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
    "use std::process",
    "use std::process; fn f() { process::exit(1) }"
);
catches!(
    use_module_then_exit,
    "process::exit",
    "use std::process; fn f() { process::exit(1) }"
);
catches!(
    use_rename_exit,
    "use std::process::exit as quit",
    "use std::process::exit as quit; fn f() { quit(1) }"
);
catches!(
    use_glob,
    "use std::process::*",
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
    "use std::process::self as p",
    "use std::process::{self as p}; fn f() { p::exit(1) }"
);
catches!(
    use_self,
    "use std::process::self",
    "use std::process::{self}; fn f() { process::exit(1) }"
);
catches!(
    use_nested_self_as,
    "use std::process::self as p",
    "use std::{process::{self as p}}; fn f() { p::exit(1) }"
);
catches!(
    use_group_exit,
    "use std::process::exit as q",
    "use std::process::{exit as q}; fn f() { q(2) }"
);
catches!(
    use_core,
    "use core::process",
    "use core::process; fn f() {}"
);
catches!(
    use_in_fn_body,
    "use std::process as p",
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
    "use tauri::process::restart",
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
    "use std::process as p",
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
