//! 平铺 `f64` ↔ `PathData` / 形状栈（格式见 crate 文档）。解码对坏输入一律回 `None`，不 panic：
//! 个数不是非负整数、越界、坐标不是有限数、读完还剩东西，都算坏输入。

use vectorcraft_geom::{Anchor, AnchorKind, FillRule, PathData, Point, SubPath};
use vectorcraft_pathops::Shape;

/// 单次输入的上限：防止前端传进一个离谱的计数让解码先分配一大块内存
const MAX_COUNT: usize = 1 << 20;

struct Reader<'a> {
    b: &'a [f64],
    i: usize,
}

impl Reader<'_> {
    fn num(&mut self) -> Option<f64> {
        let v = *self.b.get(self.i)?;
        self.i += 1;
        v.is_finite().then_some(v)
    }
    fn count(&mut self) -> Option<usize> {
        let v = self.num()?;
        if v < 0.0 || v.fract() != 0.0 || v > MAX_COUNT as f64 {
            return None;
        }
        Some(v as usize)
    }
    fn flag(&mut self) -> Option<bool> {
        let v = self.num()?;
        if v == 0.0 {
            Some(false)
        } else if v == 1.0 {
            Some(true)
        } else {
            None
        }
    }
    fn point(&mut self) -> Option<Point> {
        Some(Point::new(self.num()?, self.num()?))
    }
    fn path(&mut self) -> Option<PathData> {
        let n = self.count()?;
        let mut subpaths = Vec::with_capacity(n.min(1024));
        for _ in 0..n {
            let closed = self.flag()?;
            let k = self.count()?;
            let mut anchors = Vec::with_capacity(k.min(4096));
            for _ in 0..k {
                let p = self.point()?;
                let h_in = self.point()?;
                let h_out = self.point()?;
                let kind = if self.flag()? {
                    AnchorKind::Smooth
                } else {
                    AnchorKind::Corner
                };
                anchors.push(Anchor {
                    p,
                    h_in,
                    h_out,
                    kind,
                });
            }
            subpaths.push(SubPath { anchors, closed });
        }
        Some(PathData::new(subpaths))
    }
    fn done(&self) -> bool {
        self.i == self.b.len()
    }
}

pub fn decode_path(b: &[f64]) -> Option<PathData> {
    let mut r = Reader { b, i: 0 };
    let p = r.path()?;
    r.done().then_some(p)
}

pub fn decode_shapes(b: &[f64]) -> Option<Vec<Shape>> {
    let mut r = Reader { b, i: 0 };
    let n = r.count()?;
    let mut out = Vec::with_capacity(n.min(1024));
    for _ in 0..n {
        let rule = if r.flag()? {
            FillRule::EvenOdd
        } else {
            FillRule::NonZero
        };
        let key = r.count()? as u64;
        out.push(Shape::new(r.path()?, rule, key));
    }
    r.done().then_some(out)
}

fn push_path(o: &mut Vec<f64>, p: &PathData) {
    o.push(p.subpaths.len() as f64);
    for sp in &p.subpaths {
        o.push(if sp.closed { 1.0 } else { 0.0 });
        o.push(sp.anchors.len() as f64);
        for a in &sp.anchors {
            let smooth = if matches!(a.kind, AnchorKind::Smooth) {
                1.0
            } else {
                0.0
            };
            o.extend([
                a.p.x, a.p.y, a.h_in.x, a.h_in.y, a.h_out.x, a.h_out.y, smooth,
            ]);
        }
    }
}

pub fn encode_path(p: &PathData) -> Vec<f64> {
    let mut o = Vec::new();
    push_path(&mut o, p);
    o
}

pub fn encode_shapes(shapes: &[Shape]) -> Vec<f64> {
    let mut o = vec![shapes.len() as f64];
    for s in shapes {
        o.push(s.key as f64);
        push_path(&mut o, &s.path);
    }
    o
}
