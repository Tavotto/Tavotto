//! Tavotto 画布的路径查找器内核（ADR 0185 §五 M2）。
//!
//! 几何全部来自 `vendor/` 里随源码内联的 vectorcraft pathops（MIT OR Apache-2.0）；这一层只做两件事：
//! 把平铺的 `f64` 数组解成 `PathData`、把结果编回去。不用 wasm-bindgen——前端只需要几个函数，
//! 一套手写的 C ABI 让产物小、构建零依赖、能逐字节重建（`build.sh`）。
//!
//! # 编码（前后两个方向同一套）
//!
//! 路径：`[子路径数, (闭合 0/1, 锚点数, (px, py, in_x, in_y, out_x, out_y, 平滑 0/1)*)*]`，手柄是绝对坐标，
//! 与锚点重合 = 没有手柄。坐标单位由调用方定（前端传 pt，精度 `DEFAULT_PRECISION` 0.01 就是 0.01 pt）。
//!
//! 形状栈（路径查找器的输入，**从后往前**）：`[形状数, (填充规则 0 非零 / 1 奇偶, key, 路径…)*]`。
//! 输出：`[结果数, (key, 路径…)*]`，key 是结果沿用的那个输入形状的 key（前端传下标）。
//!
//! 每个计算函数返回输出的 `f64` 个数，结果放在 `out_ptr()` 指向的缓冲区里，下一次调用前有效；
//! 返回 0 = 输入坏了或算不出来（前端按「没有结果」处理，不会把对象删掉）。

pub mod codec;

use codec::{decode_path, decode_shapes, encode_path, encode_shapes};
use std::cell::RefCell;
use vectorcraft_pathops as ops;

/// 前端加载时核对：ABI 改了这里加一，前端 `VECOPS_ABI` 同步改，对不上就不启用路径查找器
pub const ABI_VERSION: u32 = 1;

thread_local! {
    static OUT: RefCell<Vec<f64>> = const { RefCell::new(Vec::new()) };
}

fn publish(v: Vec<f64>) -> usize {
    let n = v.len();
    OUT.with(|o| *o.borrow_mut() = v);
    n
}

/// 路径查找器：`op` 见 [`pathfinder_op`]
pub fn run_pathfinder(op: u32, input: &[f64]) -> Option<Vec<f64>> {
    let op = pathfinder_op(op)?;
    let shapes = decode_shapes(input)?;
    if shapes.is_empty() {
        return None;
    }
    Some(encode_shapes(&ops::pathfinder(op, &shapes)))
}

/// 0 合并 · 1 减去顶层 · 2 相交 · 3 排除重叠 · 4 分割 · 5 修边 · 6 合并同色 · 7 裁剪 · 8 减去后方
pub fn pathfinder_op(op: u32) -> Option<ops::PathfinderOp> {
    use ops::PathfinderOp as P;
    Some(match op {
        0 => P::Unite,
        1 => P::MinusFront,
        2 => P::Intersect,
        3 => P::Exclude,
        4 => P::Divide,
        5 => P::Trim,
        6 => P::Merge,
        7 => P::Crop,
        8 => P::MinusBack,
        _ => return None,
    })
}

fn join_of(j: u32) -> ops::Join {
    match j {
        1 => ops::Join::Round,
        2 => ops::Join::Bevel,
        _ => ops::Join::Miter,
    }
}

fn cap_of(c: u32) -> ops::Cap {
    match c {
        1 => ops::Cap::Round,
        2 => ops::Cap::Square,
        _ => ops::Cap::Butt,
    }
}

/// 偏移路径：`delta` 正 = 往外扩；join 0 斜接 / 1 圆角 / 2 斜角
pub fn run_offset(input: &[f64], delta: f64, join: u32, miter: f64) -> Option<Vec<f64>> {
    let p = decode_path(input)?;
    if !delta.is_finite() || !miter.is_finite() {
        return None;
    }
    let r = ops::try_offset_path(&p, delta, join_of(join), miter.max(1.0)).ok()?;
    (!r.is_empty()).then(|| encode_path(&r))
}

/// 轮廓化描边：描边画出来的那块面积变成一条填充路径；cap 0 平头 / 1 圆头 / 2 方头
pub fn run_outline(input: &[f64], width: f64, cap: u32, join: u32, miter: f64) -> Option<Vec<f64>> {
    let p = decode_path(input)?;
    if !(width.is_finite() && width > 0.0 && miter.is_finite()) {
        return None;
    }
    let r = ops::outline_stroke(&p, width, cap_of(cap), join_of(join), miter.max(1.0));
    (!r.is_empty()).then(|| encode_path(&r))
}

/// 简化：容差内删掉多余的锚点
pub fn run_simplify(input: &[f64], tolerance: f64) -> Option<Vec<f64>> {
    let p = decode_path(input)?;
    if !(tolerance.is_finite() && tolerance > 0.0) {
        return None;
    }
    let r = ops::simplify(&p, tolerance);
    (!r.is_empty()).then(|| encode_path(&r))
}

// ---- C ABI（只在这一段用 unsafe：前端把数据写进 alloc 出来的线性内存，再把指针传回来） ----

#[unsafe(no_mangle)]
pub extern "C" fn abi_version() -> u32 {
    ABI_VERSION
}

/// 给前端写输入用的缓冲区（`n` 个 f64）；用完调 [`dealloc`]
#[unsafe(no_mangle)]
pub extern "C" fn alloc(n: usize) -> *mut f64 {
    let mut v = vec![0.0f64; n.max(1)];
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

/// # Safety
/// `p` / `n` 必须来自同一次 [`alloc`]，且只释放一次。
#[unsafe(no_mangle)]
pub unsafe extern "C" fn dealloc(p: *mut f64, n: usize) {
    let n = n.max(1);
    unsafe { drop(Vec::from_raw_parts(p, n, n)) }
}

/// 上一次计算的结果（长度是那次的返回值）
#[unsafe(no_mangle)]
pub extern "C" fn out_ptr() -> *const f64 {
    OUT.with(|o| o.borrow().as_ptr())
}

/// # Safety
/// `p` 指向 `n` 个可读的 f64（来自 [`alloc`]）。
unsafe fn input<'a>(p: *const f64, n: usize) -> &'a [f64] {
    if p.is_null() || n == 0 {
        return &[];
    }
    unsafe { std::slice::from_raw_parts(p, n) }
}

/// # Safety
/// 见 [`input`]。
#[unsafe(no_mangle)]
pub unsafe extern "C" fn pathfinder(op: u32, p: *const f64, n: usize) -> usize {
    run_pathfinder(op, unsafe { input(p, n) }).map_or(0, publish)
}

/// # Safety
/// 见 [`input`]。
#[unsafe(no_mangle)]
pub unsafe extern "C" fn offset(
    p: *const f64,
    n: usize,
    delta: f64,
    join: u32,
    miter: f64,
) -> usize {
    run_offset(unsafe { input(p, n) }, delta, join, miter).map_or(0, publish)
}

/// # Safety
/// 见 [`input`]。
#[unsafe(no_mangle)]
pub unsafe extern "C" fn outline(
    p: *const f64,
    n: usize,
    width: f64,
    cap: u32,
    join: u32,
    miter: f64,
) -> usize {
    run_outline(unsafe { input(p, n) }, width, cap, join, miter).map_or(0, publish)
}

/// # Safety
/// 见 [`input`]。
#[unsafe(no_mangle)]
pub unsafe extern "C" fn simplify(p: *const f64, n: usize, tolerance: f64) -> usize {
    run_simplify(unsafe { input(p, n) }, tolerance).map_or(0, publish)
}
