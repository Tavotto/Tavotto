//! 退出路径的结构看护（ADR 0129）：**绕过 `RunEvent::Exit` 的退出路径必须先写 `clean`**。
//!
//! 不按子串、不按文件名单：运行时遍历 `src/` 下全部生产 `.rs`（递归；跳过 `*_tests.rs` 与
//! `#[cfg(test)]` 的项 / 模块），用 `syn` 解析成 AST，找真实的出口表达式——
//! 解析（含 `use` 别名与 glob）到 `std::process::exit` 的路径、`.restart()` / `.exit()` /
//! `.updater_builder()` 方法调用、带模块前缀的 `restart` / `updater_builder` 路径——
//! 而且**只数 `ExprPath` 与方法调用，所以注释与字符串不会误报**。
//! 允许名单只有 `main.rs` 里那几个出口函数（先写 `clean` 的那一句就在其中）。

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use syn::visit::Visit;

/// (文件相对 src 的路径, 外层函数名, 出口种类)
pub type Hit = (String, String, &'static str);

#[derive(Default)]
struct Scan {
    file: String,
    /// `use` 引入的名字 → 完整路径
    aliases: Vec<(String, Vec<String>)>,
    /// `use a::b::*` 的前缀
    globs: Vec<Vec<String>>,
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

fn flatten(tree: &syn::UseTree, prefix: &mut Vec<String>, scan: &mut Scan) {
    match tree {
        syn::UseTree::Path(p) => {
            prefix.push(p.ident.to_string());
            flatten(&p.tree, prefix, scan);
            prefix.pop();
        }
        // `use a::b::{self}` / `{self as x}`：`self` 指父路径本身（名字是父路径末段 / 别名），不是名为 self 的子项
        syn::UseTree::Name(n) if n.ident == "self" => {
            if let Some(last) = prefix.last() {
                scan.aliases.push((last.clone(), prefix.clone()));
            }
        }
        syn::UseTree::Name(n) => {
            let mut full = prefix.clone();
            full.push(n.ident.to_string());
            scan.aliases.push((n.ident.to_string(), full));
        }
        syn::UseTree::Rename(r) if r.ident == "self" => {
            scan.aliases.push((r.rename.to_string(), prefix.clone()));
        }
        syn::UseTree::Rename(r) => {
            let mut full = prefix.clone();
            full.push(r.ident.to_string());
            scan.aliases.push((r.rename.to_string(), full));
        }
        syn::UseTree::Glob(_) => scan.globs.push(prefix.clone()),
        syn::UseTree::Group(g) => {
            for t in &g.items {
                flatten(t, prefix, scan);
            }
        }
    }
}

impl Scan {
    fn hit(&mut self, kind: &'static str) {
        let f = self.fns.last().cloned().unwrap_or_default();
        self.hits.push((self.file.clone(), f, kind));
    }

    /// 路径可能指向的全部完整写法（别名展开 + glob 前缀展开）。
    fn candidates(&self, segs: &[String]) -> Vec<Vec<String>> {
        let mut out = vec![segs.to_vec()];
        if let Some((_, full)) = self.aliases.iter().find(|(n, _)| *n == segs[0]) {
            let mut v = full.clone();
            v.extend_from_slice(&segs[1..]);
            out.push(v);
        }
        for g in &self.globs {
            let mut v = g.clone();
            v.extend_from_slice(segs);
            out.push(v);
        }
        out
    }

    fn classify_path(&mut self, segs: &[String]) {
        if segs.is_empty() {
            return;
        }
        let last = segs[segs.len() - 1].as_str();
        let cands = self.candidates(segs);
        let is_std_exit = |c: &Vec<String>| {
            let n = c.len();
            n >= 3
                && c[n - 1] == "exit"
                && c[n - 2] == "process"
                && matches!(c[0].as_str(), "std" | "core")
        };
        if cands.iter().any(is_std_exit) {
            self.hit("process::exit");
        }
        // 带模块前缀（或 use 展开后有前缀）的 restart / updater_builder；
        // 单段且没有别名的 `updater_builder(..)` 是 main.rs 自己的助手，不算
        // （glob 只对 `restart` 算数：`updater_builder` 是 trait 方法，不会经 glob 变成自由函数）
        let n_glob = self.globs.len();
        let explicit = &cands[..cands.len() - n_glob];
        let pool = if last == "restart" {
            &cands[..]
        } else {
            explicit
        };
        if matches!(last, "restart" | "updater_builder")
            && pool.iter().any(|c| c.len() >= 2 && c[c.len() - 1] == last)
        {
            self.hit(if last == "restart" {
                "restart"
            } else {
                "updater_builder"
            });
        }
    }

    /// 宏体 syn 不解析：按词扫（`std::process::exit(1)` 藏在自定义宏参数里也逃不掉）。
    fn scan_tokens(&mut self, tokens: &str) {
        for word in tokens.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
            match word {
                "exit" => self.hit("process::exit"),
                "restart" => self.hit("restart"),
                "updater_builder" => self.hit("updater_builder"),
                _ => {}
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
        flatten(&u.tree, &mut Vec::new(), self);
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
        let segs: Vec<String> = p
            .path
            .segments
            .iter()
            .map(|s| s.ident.to_string())
            .collect();
        self.classify_path(&segs);
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
        self.scan_tokens(&m.tokens.to_string());
        syn::visit::visit_macro(self, m);
    }
}

/// 扫一段源码（单测直接喂片段）。
pub fn scan_source(file: &str, src: &str) -> Vec<Hit> {
    let ast = syn::parse_file(src).unwrap_or_else(|e| panic!("{file} 解析失败: {e}"));
    // `use` 要先于使用点收齐（函数内的 use 也算），所以先走一遍只收 use
    let mut scan = Scan {
        file: file.to_string(),
        ..Scan::default()
    };
    struct Uses<'a>(&'a mut Scan);
    impl<'ast> Visit<'ast> for Uses<'_> {
        fn visit_item(&mut self, i: &'ast syn::Item) {
            if let syn::Item::Mod(m) = i {
                if is_cfg_test(&m.attrs) {
                    return;
                }
            }
            if let syn::Item::Use(u) = i {
                if !is_cfg_test(&u.attrs) {
                    flatten(&u.tree, &mut Vec::new(), self.0);
                }
                return;
            }
            syn::visit::visit_item(self, i);
        }
    }
    Uses(&mut scan).visit_file(&ast);
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
    // 允许名单：先写 clean 的出口函数本身（顺序由 main.rs 的结构测试另行钉住）
    let allowed: [Hit; 3] = [
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

#[test]
fn the_scanner_sees_every_way_of_writing_an_exit() {
    let kinds = |src: &str| -> Vec<&'static str> {
        scan_source("x.rs", src).into_iter().map(|h| h.2).collect()
    };
    for src in [
        "fn f() { std::process::exit(1); }",
        "fn f() { ::std::process::exit(1) }",
        "use std::process; fn f() { process::exit(1) }",
        "use std::process::exit as quit; fn f() { quit(1) }",
        "use std::process::*; fn f() { exit(1) }",
        "use std::*; fn f() { process::exit(1) }",
        // 每种写法单独一条：同一条里混两种写法，一个被认出就会掩盖另一个漏报
        "use std::process::{self as p}; fn f() { p::exit(1) }",
        "use std::process::{self}; fn f() { process::exit(1) }",
        "use std::{process::{self as p}}; fn f() { p::exit(1) }",
        "use std::process::{exit as q}; fn f() { q(2) }",
        "fn f() { let g = std::process::exit; g(1) }",
        "fn f() { std :: process\n  :: exit ( 1 ) }",
        "fn f() { println!(\"{}\", std::process::exit(1)) }",
        "fn f(a: A) { a.exit(0) }",
    ] {
        assert!(kinds(src).contains(&"process::exit"), "漏报: {src}");
    }
    for (src, kind) in [
        ("fn f(h: H) { h.restart() }", "restart"),
        (
            "use tauri::process::restart; fn f() { restart(&e) }",
            "restart",
        ),
        ("fn f() { tauri::process::restart(&e) }", "restart"),
        ("fn f(a: A) { a.updater_builder() }", "updater_builder"),
        (
            "fn f(a: A) { UpdaterExt::updater_builder(&a) }",
            "updater_builder",
        ),
    ] {
        assert!(kinds(src).contains(&kind), "漏报: {src}");
    }
    // 不误报：注释 / 字符串 / 本地助手 / 无关的 exit 名字 / cfg(test)
    for src in [
        "// std::process::exit(1)\nfn f() { let _ = \"std::process::exit(1)\"; }",
        "fn f() { updater_builder(&a) }",
        "fn exit() {} fn f() { exit() }",
        "#[cfg(test)] mod tests { fn f() { std::process::exit(1) } }",
        "#[cfg(test)] fn f() { std::process::exit(1) }",
    ] {
        assert!(kinds(src).is_empty(), "误报: {src}");
    }
}
