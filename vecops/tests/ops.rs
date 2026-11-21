//! 内核用例：面积对得上解析解、key 归属对、坏输入不 panic 只回 None。

use tavotto_vecops::codec::{decode_path, encode_path};
use tavotto_vecops::{run_offset, run_outline, run_pathfinder, run_simplify};
use vectorcraft_geom::{FillRule, PathData};
use vectorcraft_pathops::area;

const K: f64 = 0.552_284_749_830_793_4;

fn circle(cx: f64, cy: f64, r: f64) -> Vec<f64> {
    let mut f = vec![1.0, 1.0, 4.0];
    for (px, py, tx, ty) in [
        (cx + r, cy, 0.0, 1.0),
        (cx, cy + r, -1.0, 0.0),
        (cx - r, cy, 0.0, -1.0),
        (cx, cy - r, 1.0, 0.0),
    ] {
        f.extend([
            px,
            py,
            px - tx * r * K,
            py - ty * r * K,
            px + tx * r * K,
            py + ty * r * K,
            1.0,
        ]);
    }
    f
}

fn rect(x: f64, y: f64, w: f64, h: f64) -> Vec<f64> {
    let mut f = vec![1.0, 1.0, 4.0];
    for (px, py) in [(x, y), (x + w, y), (x + w, y + h), (x, y + h)] {
        f.extend([px, py, px, py, px, py, 0.0]);
    }
    f
}

/// 形状栈：(奇偶?, 路径) 从后往前，key = 下标
fn stack(items: &[(bool, Vec<f64>)]) -> Vec<f64> {
    let mut o = vec![items.len() as f64];
    for (i, (eo, p)) in items.iter().enumerate() {
        o.push(if *eo { 1.0 } else { 0.0 });
        o.push(i as f64);
        o.extend(p);
    }
    o
}

/// 输出 → [(key, PathData)]
fn results(out: &[f64]) -> Vec<(u64, PathData)> {
    let n = out[0] as usize;
    let mut i = 1;
    let mut r = Vec::new();
    for _ in 0..n {
        let key = out[i] as u64;
        i += 1;
        let start = i;
        let ns = out[i] as usize;
        i += 1;
        for _ in 0..ns {
            let k = out[i + 1] as usize;
            i += 2 + 7 * k;
        }
        r.push((key, decode_path(&out[start..i]).expect("合法输出")));
    }
    assert_eq!(i, out.len(), "输出读完正好用尽");
    r
}

fn total_area(rs: &[(u64, PathData)]) -> f64 {
    rs.iter()
        .map(|(_, p)| area(p, FillRule::NonZero).abs())
        .sum()
}

/// 两个半径 r、圆心距 d 的圆的重叠面积
fn lens(r: f64, d: f64) -> f64 {
    2.0 * r * r * (d / (2.0 * r)).acos() - d / 2.0 * (4.0 * r * r - d * d).sqrt()
}

fn close(a: f64, b: f64, rel: f64) {
    assert!((a - b).abs() <= rel * b.abs().max(1.0), "{a} vs {b}");
}

#[test]
fn two_circles_shape_modes() {
    let (r, d) = (10.0, 12.0);
    let disk = std::f64::consts::PI * r * r;
    let ov = lens(r, d);
    let s = stack(&[(false, circle(0.0, 0.0, r)), (false, circle(d, 0.0, r))]);
    // 贝塞尔圆本身比真圆大约 0.03%，容差取 0.5%
    let unite = results(&run_pathfinder(0, &s).expect("合并"));
    assert_eq!(unite.len(), 1);
    assert_eq!(unite[0].0, 1, "合并沿用最前面那个的样式");
    close(total_area(&unite), 2.0 * disk - ov, 0.005);

    let minus = results(&run_pathfinder(1, &s).expect("减去顶层"));
    assert_eq!(minus.len(), 1);
    assert_eq!(minus[0].0, 0, "减去顶层沿用最后面那个的样式");
    close(total_area(&minus), disk - ov, 0.005);

    let inter = results(&run_pathfinder(2, &s).expect("相交"));
    close(total_area(&inter), ov, 0.005);

    let excl = results(&run_pathfinder(3, &s).expect("排除"));
    close(total_area(&excl), 2.0 * (disk - ov), 0.005);

    let divide = results(&run_pathfinder(4, &s).expect("分割"));
    assert_eq!(divide.len(), 3, "两圆相交分成三块");
    close(total_area(&divide), 2.0 * disk - ov, 0.005);
    let lens_piece = divide
        .iter()
        .filter(|(k, _)| *k == 1)
        .map(|(_, p)| area(p, FillRule::NonZero).abs());
    assert!(
        lens_piece.into_iter().any(|a| (a - ov).abs() < 0.005 * ov),
        "重叠那块归前面的圆"
    );

    let back = results(&run_pathfinder(8, &s).expect("减去后方"));
    assert_eq!(back[0].0, 1);
    close(total_area(&back), disk - ov, 0.005);
}

#[test]
fn rect_minus_inner_circle_makes_a_hole() {
    let s = stack(&[
        (false, rect(0.0, 0.0, 40.0, 40.0)),
        (false, circle(20.0, 20.0, 10.0)),
    ]);
    let out = results(&run_pathfinder(1, &s).expect("减去顶层"));
    assert_eq!(out.len(), 1);
    assert_eq!(out[0].1.subpaths.len(), 2, "外框 + 一个洞");
    close(
        total_area(&out),
        1600.0 - std::f64::consts::PI * 100.0,
        0.005,
    );
}

#[test]
fn even_odd_input_is_respected() {
    // 一个子路径里两个同向的方框：非零 = 整块，奇偶 = 中间是洞
    let mut p = vec![2.0];
    for (x, w) in [(0.0, 30.0), (10.0, 10.0)] {
        p.extend([1.0, 4.0]);
        for (px, py) in [(x, x), (x + w, x), (x + w, x + w), (x, x + w)] {
            p.extend([px, py, px, py, px, py, 0.0]);
        }
    }
    let nz = results(&run_pathfinder(0, &stack(&[(false, p.clone())])).expect("非零"));
    let eo = results(&run_pathfinder(0, &stack(&[(true, p)])).expect("奇偶"));
    close(total_area(&nz), 900.0, 1e-6);
    close(total_area(&eo), 800.0, 1e-6);
}

#[test]
fn offset_outline_simplify() {
    let c = circle(0.0, 0.0, 10.0);
    let grown = decode_path(&run_offset(&c, 2.0, 1, 4.0).expect("偏移")).expect("合法");
    close(
        area(&grown, FillRule::NonZero).abs(),
        std::f64::consts::PI * 144.0,
        0.01,
    );

    let line = vec![
        1.0, 0.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 20.0, 0.0, 20.0, 0.0, 20.0, 0.0, 0.0,
    ];
    let band = decode_path(&run_outline(&line, 2.0, 0, 0, 4.0).expect("轮廓化")).expect("合法");
    close(area(&band, FillRule::NonZero).abs(), 40.0, 1e-6);

    let s = decode_path(&run_simplify(&c, 0.5).expect("简化")).expect("合法");
    assert!(!s.is_empty());
}

#[test]
fn bad_input_is_none_not_panic() {
    assert!(run_pathfinder(0, &[]).is_none());
    assert!(
        run_pathfinder(99, &stack(&[(false, circle(0.0, 0.0, 1.0))])).is_none(),
        "未知操作"
    );
    assert!(
        run_pathfinder(0, &[1.0, 0.0, 0.0, 1.0, 1.0, 3.0]).is_none(),
        "锚点不够读"
    );
    assert!(
        run_pathfinder(0, &[1.0, 0.5, 0.0]).is_none(),
        "规则不是 0 / 1"
    );
    assert!(run_pathfinder(0, &[1e12]).is_none(), "离谱的计数");
    let mut nan = circle(0.0, 0.0, 1.0);
    nan[3] = f64::NAN;
    assert!(decode_path(&nan).is_none());
    let mut extra = circle(0.0, 0.0, 1.0);
    extra.push(0.0);
    assert!(decode_path(&extra).is_none(), "读完还剩东西");
    assert!(run_offset(&circle(0.0, 0.0, 1.0), f64::INFINITY, 0, 4.0).is_none());
    assert!(run_outline(&circle(0.0, 0.0, 1.0), 0.0, 0, 0, 4.0).is_none());
}

#[test]
fn encode_round_trips() {
    let c = circle(3.0, 4.0, 5.0);
    assert_eq!(encode_path(&decode_path(&c).expect("合法")), c);
}
