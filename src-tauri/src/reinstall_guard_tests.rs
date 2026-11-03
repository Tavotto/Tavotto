//! 「重新安装」放宽版本判据的结构看护（ADR 0130 三之二）：同版本安装只能经过唯一一条路径。
//!
//! 主语：**全部生产 `.rs`**（递归 `src/`；跳过 `*_tests.rs` 与 `#[cfg(test)]` 的项 / 模块）里**真实的 AST 节点**，
//! 不是子串——注释、字符串、`version_comparator /*x*/ (` 这种插注释的写法、`use … as` 改名后的调用都在判据之内。
//! 与 `exit_guard_tests.rs` 同一套 `syn` 做法，同样不做 `use` 别名解析，改从结构上消掉问题面：
//!
//! - `.version_comparator(..)` 方法调用：全壳恰好一处，外层 fn 是 `reinstall_builder`；
//! - `run_reinstall` / `update_reinstall` / `reinstall_builder` 的**任何形式的引用**（调用、当函数值传、路径、方法名、
//!   `use` 导入、宏体里的标识符）都算一次命中，每一个都只许出现在规定的外层 fn 里；
//! - 把这三个名字（或 `version_comparator`）`use … as` 改名导入，一律违规——改名之后的引用没法再追踪。
//!
//! 命中与允许名单按**多重集**精确相等：多一处、少一处都失败，失败信息写清文件和外层 fn。
//! 盲点（写在明处）：宏体 syn 不解析，只看标识符；另有人在别的 crate / build.rs 里调用不在范围内（壳只有这一个 crate）。

use std::path::{Path, PathBuf};

use proc_macro2::{TokenStream, TokenTree};
use syn::ext::IdentExt;
use syn::visit::Visit;

/// (文件相对 src 的路径, 外层 fn 名, 被引用的名字)
type Hit = (String, String, String);

const WATCHED: [&str; 4] = [
    "version_comparator",
    "run_reinstall",
    "update_reinstall",
    "reinstall_builder",
];

/// 唯一允许的命中。
fn allowed() -> Vec<Hit> {
    let h = |f: &str, o: &str, n: &str| (f.to_string(), o.to_string(), n.to_string());
    vec![
        h("app_update.rs", "reinstall_builder", "version_comparator"),
        h("main.rs", "update_reinstall", "run_reinstall"),
        h("rescue.rs", "rescue_reinstall", "update_reinstall"),
        h("app_update.rs", "run_reinstall", "reinstall_builder"),
        h("app_update.rs", "run_reinstall_check", "reinstall_builder"),
    ]
}

#[derive(Default)]
struct Scan {
    file: String,
    fns: Vec<String>,
    hits: Vec<Hit>,
}

fn is_cfg_test(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| match &a.meta {
        syn::Meta::List(l) if l.path.is_ident("cfg") => {
            syn::parse2::<syn::Ident>(l.tokens.clone()).is_ok_and(|i| i == "test")
        }
        _ => false,
    })
}

impl Scan {
    fn hit(&mut self, name: &str) {
        let f = self.fns.last().cloned().unwrap_or_default();
        self.hits.push((self.file.clone(), f, name.to_string()));
    }

    fn watched(name: &str) -> bool {
        WATCHED.contains(&name)
    }

    fn scan_tokens(&mut self, ts: TokenStream) {
        for tt in ts {
            match tt {
                TokenTree::Group(g) => self.scan_tokens(g.stream()),
                TokenTree::Ident(i) => {
                    let n = i.unraw().to_string();
                    if Self::watched(&n) {
                        self.hit(&n);
                    }
                }
                TokenTree::Literal(_) | TokenTree::Punct(_) => {}
            }
        }
    }

    /// `use` 叶子：原名在监视名单里就算一次命中；带改名（`as`）的另记一次，永远无法匹配允许名单。
    fn use_tree(&mut self, tree: &syn::UseTree) {
        match tree {
            syn::UseTree::Path(p) => self.use_tree(&p.tree),
            syn::UseTree::Name(n) => {
                let id = n.ident.unraw().to_string();
                if Self::watched(&id) {
                    self.hit(&format!("use {id}"));
                }
            }
            syn::UseTree::Rename(r) => {
                let id = r.ident.unraw().to_string();
                if Self::watched(&id) {
                    self.hit(&format!("use {id} as {}", r.rename.unraw()));
                }
            }
            syn::UseTree::Glob(_) => {}
            syn::UseTree::Group(g) => {
                for t in &g.items {
                    self.use_tree(t);
                }
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
        self.use_tree(&u.tree);
    }

    fn visit_item_fn(&mut self, f: &'ast syn::ItemFn) {
        self.fns.push(f.sig.ident.unraw().to_string());
        syn::visit::visit_item_fn(self, f);
        self.fns.pop();
    }

    fn visit_impl_item_fn(&mut self, f: &'ast syn::ImplItemFn) {
        if is_cfg_test(&f.attrs) {
            return;
        }
        self.fns.push(f.sig.ident.unraw().to_string());
        syn::visit::visit_impl_item_fn(self, f);
        self.fns.pop();
    }

    /// 任何路径（表达式、类型、模式……）：末段是监视名就算命中；调用、当函数值传都落在这里。
    fn visit_path(&mut self, p: &'ast syn::Path) {
        if let Some(last) = p.segments.last() {
            let n = last.ident.unraw().to_string();
            if Self::watched(&n) {
                self.hit(&n);
            }
        }
        syn::visit::visit_path(self, p);
    }

    fn visit_expr_method_call(&mut self, m: &'ast syn::ExprMethodCall) {
        let n = m.method.unraw().to_string();
        if Self::watched(&n) {
            self.hit(&n);
        }
        syn::visit::visit_expr_method_call(self, m);
    }

    fn visit_macro(&mut self, m: &'ast syn::Macro) {
        // 宏体 syn 不解析：遍历 TokenTree 只看标识符（`visit_macro` 自己还会访问宏的 path）
        self.scan_tokens(m.tokens.clone());
        syn::visit::visit_macro(self, m);
    }
}

fn scan_source(file: &str, src: &str) -> Vec<Hit> {
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

fn production_hits() -> Vec<Hit> {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rs_files(&src, &mut files);
    let mut hits = Vec::new();
    for f in files {
        if f.file_name()
            .is_some_and(|n| n.to_string_lossy().ends_with("_tests.rs"))
        {
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

fn diff(mut got: Vec<Hit>, mut want: Vec<Hit>) -> String {
    got.sort();
    want.sort();
    let mut extra = Vec::new();
    let mut rest = want.clone();
    for g in &got {
        match rest.iter().position(|w| w == g) {
            Some(i) => {
                rest.remove(i);
            }
            None => extra.push(g.clone()),
        }
    }
    let show = |v: &[Hit]| {
        v.iter()
            .map(|(f, o, n)| format!("{f} 的 fn {o:?} 里引用了 {n}"))
            .collect::<Vec<_>>()
            .join("；")
    };
    if extra.is_empty() && rest.is_empty() {
        String::new()
    } else {
        format!("违规：[{}]；缺少：[{}]", show(&extra), show(&rest))
    }
}

#[test]
fn same_version_install_is_reachable_only_through_the_one_allowed_path() {
    let d = diff(production_hits(), allowed());
    assert!(d.is_empty(), "{d}");
}

#[test]
fn the_scanner_sees_commented_aliased_and_value_position_references() {
    // 判据自检：三种绕过子串判据的写法都必须被 AST 抓到
    let commented = scan_source(
        "x.rs",
        "fn other() { let _ = b().version_comparator /* x */ (|a, b| true); }",
    );
    assert_eq!(
        commented,
        [("x.rs".into(), "other".into(), "version_comparator".into())]
    );
    let aliased = scan_source(
        "x.rs",
        "use crate::update_reinstall as u;\nfn other() { u(); }",
    );
    assert!(aliased.iter().any(|h| h.2 == "use update_reinstall as u"));
    let as_value = scan_source(
        "x.rs",
        "fn other() { let f = app_update::run_reinstall; let _ = f; }",
    );
    assert_eq!(
        as_value,
        [("x.rs".into(), "other".into(), "run_reinstall".into())]
    );
    // 注释 / 字符串 / 测试模块里的不算
    let quiet = scan_source(
        "x.rs",
        "// version_comparator(\nfn f() { let _ = \"run_reinstall\"; }\n#[cfg(test)]\nmod t { fn g() { run_reinstall(); } }",
    );
    assert!(quiet.is_empty(), "{quiet:?}");
}
