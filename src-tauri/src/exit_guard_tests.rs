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

use std::path::{Path, PathBuf};

use proc_macro2::{TokenStream, TokenTree};
use syn::ext::IdentExt;
use syn::visit::Visit;

/// (文件相对 src 的路径, 外层函数名, 出口种类)
pub type Hit = (String, String, String);

#[derive(Default)]
struct Scan {
    file: String,
    fns: Vec<String>,
    hits: Vec<Hit>,
}

/// 只认**整串就是** `#[cfg(test)]` 的项：`cfg(not(test))`、`cfg(any(test, ..))`、`cfg(all(..))` 等
/// 都可能在生产构建里生效，一律照扫（宁可多扫，不可漏扫）。
fn is_cfg_test(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| match &a.meta {
        syn::Meta::List(l) if l.path.is_ident("cfg") => {
            syn::parse2::<syn::Ident>(l.tokens.clone()).is_ok_and(|i| i == "test")
        }
        _ => false,
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
            path.push(p.ident.unraw().to_string());
            use_leaves(&p.tree, path, out);
            path.pop();
        }
        syn::UseTree::Name(n) => {
            let mut full = path.clone();
            full.push(n.ident.unraw().to_string());
            let d = show(&full, None);
            out.push((full, d));
        }
        syn::UseTree::Rename(r) => {
            let mut full = path.clone();
            full.push(r.ident.unraw().to_string());
            let d = show(&full, Some(r.rename.unraw().to_string()));
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
                TokenTree::Ident(i) => match i.unraw().to_string().as_str() {
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

    fn visit_expr_path(&mut self, p: &'ast syn::ExprPath) {
        let n = p.path.segments.len();
        match p.path.segments[n - 1].ident.unraw().to_string().as_str() {
            "exit" => self.hit("process::exit"),
            "restart" => self.hit("restart"),
            // 单段 `updater_builder(..)` 是 main.rs 自己的助手；带前缀的（`UpdaterExt::updater_builder(&a)`）才是绕过
            "updater_builder" if n >= 2 => self.hit("updater_builder"),
            _ => {}
        }
        syn::visit::visit_expr_path(self, p);
    }

    fn visit_expr_method_call(&mut self, m: &'ast syn::ExprMethodCall) {
        match m.method.unraw().to_string().as_str() {
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

/// 全部生产 `.rs` 里的出口命中：**只**跳过文件名以 `_tests.rs` 结尾的文件（它们必须且只能在 `#[cfg(test)]` 下被声明，
/// 由 [`test_files_are_exactly_the_cfg_test_mod_targets`] 钉住）。不根据 `mod` 声明按文件名去跳——同名的生产文件会被误跳。
pub fn production_hits() -> Vec<Hit> {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    rs_files(&src, &mut files);
    let mut hits = Vec::new();
    for f in files {
        if is_tests_file(&f) {
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

fn is_tests_file(f: &Path) -> bool {
    f.file_name()
        .is_some_and(|n| n.to_string_lossy().ends_with("_tests.rs"))
}

/// 一条文件模块声明 `mod x;`：解析到的文件（存在才有）、是否整串 `#[cfg(test)]`。
#[derive(Debug)]
struct ModDecl {
    target: Option<PathBuf>,
    cfg_test: bool,
    name: String,
}

fn path_attr(attrs: &[syn::Attribute]) -> Option<String> {
    attrs.iter().find_map(|a| match &a.meta {
        syn::Meta::NameValue(nv) if nv.path.is_ident("path") => match &nv.value {
            syn::Expr::Lit(syn::ExprLit {
                lit: syn::Lit::Str(s),
                ..
            }) => Some(s.value()),
            _ => None,
        },
        _ => None,
    })
}

/// 按 Rust 的模块路径规则解析 `file` 里全部 `mod x;`（含内联 `mod a { mod x; }` 的嵌套）：
/// `main.rs` / `lib.rs` / `mod.rs` 的子模块在同目录，其余文件 `foo.rs` 的在 `foo/`；
/// `x` → `<dir>/x.rs` 或 `<dir>/x/mod.rs`；`#[path = "p"]` 相对声明所在文件的目录。
fn resolve_mod_decls(file: &Path) -> Vec<ModDecl> {
    fn walk(items: &[syn::Item], file_dir: &Path, mod_dir: &Path, out: &mut Vec<ModDecl>) {
        for item in items {
            let syn::Item::Mod(m) = item else { continue };
            let name = m.ident.unraw().to_string();
            match &m.content {
                Some((_, inner)) => {
                    let sub = match path_attr(&m.attrs) {
                        Some(p) => file_dir.join(p),
                        None => mod_dir.join(&name),
                    };
                    walk(inner, file_dir, &sub, out);
                }
                None => {
                    let target = match path_attr(&m.attrs) {
                        Some(p) => Some(file_dir.join(p)).filter(|f| f.is_file()),
                        None => [
                            mod_dir.join(format!("{name}.rs")),
                            mod_dir.join(&name).join("mod.rs"),
                        ]
                        .into_iter()
                        .find(|f| f.is_file()),
                    };
                    out.push(ModDecl {
                        target,
                        cfg_test: is_cfg_test(&m.attrs),
                        name,
                    });
                }
            }
        }
    }
    let ast = syn::parse_file(&std::fs::read_to_string(file).unwrap()).unwrap();
    let dir = file.parent().unwrap();
    let stem = file.file_stem().unwrap().to_string_lossy().into_owned();
    let mod_dir = if matches!(stem.as_str(), "main" | "lib" | "mod") {
        dir.to_path_buf()
    } else {
        dir.join(&stem)
    };
    let mut out = Vec::new();
    walk(&ast.items, dir, &mod_dir, &mut out);
    out
}

/// 两个方向钉住「被跳过的文件」集合：
/// (a) 每个 `#[cfg(test)] mod x;` 指向的文件都叫 `*_tests.rs`（否则它在生产扫描里不会被跳过也无妨，但命名约定要守住）；
/// (b) 每个 `*_tests.rs` 都只在 `#[cfg(test)]` 下被声明，且至少被声明一次（没人声明 = 死文件，也可能是路径规则没读对）。
/// 于是扫描器跳过的文件与 cfg(test) 模块目标一一对应，不存在「被跳过的生产文件」。
fn check_test_file_pairing(src: &Path) -> Vec<String> {
    let mut files = Vec::new();
    rs_files(src, &mut files);
    let mut decls: Vec<(PathBuf, ModDecl)> = Vec::new();
    for f in &files {
        for d in resolve_mod_decls(f) {
            decls.push((f.clone(), d));
        }
    }
    let mut problems = Vec::new();
    for (from, d) in &decls {
        if d.cfg_test {
            if let Some(t) = &d.target {
                if !is_tests_file(t) {
                    problems.push(format!(
                        "{} 里 #[cfg(test)] mod {}; 指向 {}，文件名必须以 _tests.rs 结尾",
                        from.display(),
                        d.name,
                        t.display()
                    ));
                }
            }
        }
    }
    for f in files.iter().filter(|f| is_tests_file(f)) {
        let declared: Vec<&(PathBuf, ModDecl)> = decls
            .iter()
            .filter(|(_, d)| d.target.as_deref() == Some(f.as_path()))
            .collect();
        if declared.is_empty() {
            problems.push(format!("{} 没有被任何 mod 声明", f.display()));
        }
        for (from, d) in declared {
            if !d.cfg_test {
                problems.push(format!(
                    "{} 在 {} 里被声明为 mod {}，但没有 #[cfg(test)]：生产扫描会跳过它",
                    f.display(),
                    from.display(),
                    d.name
                ));
            }
        }
    }
    problems
}

#[test]
fn test_files_are_exactly_the_cfg_test_mod_targets() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let problems = check_test_file_pairing(&src);
    assert!(problems.is_empty(), "{problems:#?}");
}

/// 模块路径规则的自测：在隔离临时目录里造树，覆盖同目录、`x/mod.rs`、子模块目录、`#[path]`、内联嵌套。
#[test]
fn the_pairing_check_follows_rust_module_path_rules() {
    let root = std::env::temp_dir().join(format!(
        "tavotto-exit-guard-{}-{:016x}",
        std::process::id(),
        rand::random::<u64>()
    ));
    let w = |rel: &str, body: &str| {
        let p = root.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, body).unwrap();
    };
    // 干净的树：flat、子目录、#[path]
    w("main.rs", "#[cfg(test)]\nmod a_tests;\nmod b;\n");
    w("a_tests.rs", "");
    w(
        "b.rs",
        "#[cfg(test)]\nmod c_tests;\n#[cfg(test)]\n#[path = \"d_tests.rs\"]\nmod dd;\n",
    );
    w("b/c_tests.rs", "");
    w("d_tests.rs", "");
    assert!(check_test_file_pairing(&root).is_empty());
    // (a) cfg(test) 指向非 _tests.rs
    w("b.rs", "#[cfg(test)]\nmod c;\n");
    w("b/c.rs", "");
    w("b/c_tests.rs", "");
    w("d_tests.rs", "");
    let p = check_test_file_pairing(&root);
    assert!(
        p.iter().any(|s| s.contains("必须以 _tests.rs 结尾")),
        "{p:?}"
    );
    // (b) *_tests.rs 被无 cfg(test) 地声明 / 没人声明
    w("b.rs", "mod c_tests;\n");
    w("b/c.rs", "");
    let p = check_test_file_pairing(&root);
    assert!(p.iter().any(|s| s.contains("没有 #[cfg(test)]")), "{p:?}");
    assert!(
        p.iter()
            .any(|s| s.contains("d_tests.rs 没有被任何 mod 声明")),
        "{p:?}"
    );
    // 同名生产文件在别的目录里：不再按 stem 误判
    std::fs::remove_dir_all(&root).unwrap();
    w("main.rs", "mod x;\nmod sub;\n#[cfg(test)]\nmod y_tests;\n");
    w("x.rs", "");
    w("y_tests.rs", "");
    w("sub/mod.rs", "mod x;\n");
    w("sub/x.rs", "");
    assert!(check_test_file_pairing(&root).is_empty());
    let _ = std::fs::remove_dir_all(&root);
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
        // 原生救援页「重新尝试」（ADR 0130）：mark_clean_exit 在 restart 之前，由 main.rs 的结构测试钉住
        h("rescue.rs", "rescue_retry", "restart"),
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
            "run_cli_json",
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

// 只在生产构建生效 / 测试与生产都生效的 cfg 不许被当成测试代码跳过（#1070 r4280166858）
catches!(
    cfg_not_test_fn,
    "process::exit",
    "#[cfg(not(test))] fn f() { std::process::exit(1) }"
);
catches!(
    cfg_any_test_fn,
    "process::exit",
    "#[cfg(any(test, windows))] fn f() { std::process::exit(1) }"
);
catches!(
    cfg_not_test_mod,
    "process::exit",
    "#[cfg(not(test))] mod m { fn f() { std::process::exit(1) } }"
);

// 原始标识符（r#exit / r#restart）与普通写法同名，按 unraw 后的名字判（#1070 r4280464772）
catches!(
    raw_ident_exit_path,
    "process::exit",
    "fn f() { std::process::r#exit(1) }"
);
catches!(
    raw_ident_exit_method,
    "process::exit",
    "fn f(a: A) { a.r#exit(0) }"
);
catches!(
    raw_ident_restart_method,
    "restart",
    "fn f(h: H) { h.r#restart() }"
);
catches!(
    raw_ident_in_macro,
    "process::exit",
    "fn f() { m!(std::process::r#exit(1)) }"
);
