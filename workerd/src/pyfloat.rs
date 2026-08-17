//! CPython `repr(float)` 兼容的浮点格式化——**canonical_json 逐字节对齐的关键**。
//!
//! Python 的 `json.dumps` 写浮点走的就是 `float.__repr__`
//! （C 编码器里的 `PyOS_double_to_string(v, 'r', 0, Py_DTSF_ADD_DOT_0, NULL)`）。
//! Rust 这边没有现成等价物：
//!
//! * `{}`（Display）永不用指数写法——`1e22` 会打成 23 位的一长串数字；
//! * `{:e}`（LowerExp）永远用指数写法，且指数不带符号也不补零——`1e-7` 而不是 `1e-07`；
//! * `serde_json` 内部用 ryu，写出来是 `1e22` / `1e-7` / `0.3`，三样都对不上。
//!
//! 但**有效数字本身是一致的**：Rust 的 `{:e}` 与 CPython 的 `_Py_dg_dtoa(mode=0)`
//! 都给「最短且能往返回同一个 double」的十进制，半程点一律进偶。所以这里不重新
//! 算数字，只把 `{:e}` 的结果按 CPython 的写法重排：
//!
//! 1. 从 `{:e}` 拆出有效数字 `digits` 与十进制指数，换算成 CPython 里的 `decpt`
//!    （满足 `value = 0.digits × 10^decpt`）；
//! 2. `decpt <= -4 || decpt > 16` 用指数写法，否则定点写法
//!    （CPython `format_float_short` 里 'r' 分支的判据）；
//! 3. 定点写法在「看起来像整数」时补 `.0`（`Py_DTSF_ADD_DOT_0`），
//!    所以 `1.0` 写成 `1.0`、`-0.0` 保号写成 `-0.0`；
//! 4. 指数写法的指数是 `decpt - 1`，按 `%+.02d` 输出——**永远带符号、至少两位**，
//!    于是 `1e+22` / `1e-07`。
//!
//! 这四条改任何一条都会让 patch 身份与 Python 侧分叉，golden vectors 的
//! `floats` 一组就是钉这个的。

/// 定点/指数的分界（CPython `format_float_short` 的 'r' 分支）。
const EXP_UPPER: i32 = 16;
const EXP_LOWER: i32 = -4;

/// 有限 f64 → 与 `repr(float)` 逐字节相同的字符串。
///
/// 非有限值（NaN / ±Inf）不是 JSON 值，规范化时已经剔除；真传进来也不该
/// 悄悄写出 `NaN` 这种没人能解析的东西，直接给一个显眼的占位。
pub fn format_f64(v: f64) -> String {
    if !v.is_finite() {
        return "null".to_string();
    }
    let exp_form = format!("{:e}", v); // 形如 "-3.0000000000000004e-1" / "0e0"
    let (mantissa, exp_text) = match exp_form.split_once('e') {
        Some(pair) => pair,
        None => return exp_form, // 不可能——{:e} 一定有 e；不 panic 而已
    };
    let exp: i32 = exp_text.parse().unwrap_or(0);
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(|c| c.is_ascii_digit()).collect();
    // decpt：小数点应当落在 digits 的第几位之后（value = 0.digits × 10^decpt）。
    let decpt = exp + 1;

    let body = if decpt <= EXP_LOWER || decpt > EXP_UPPER {
        format_exponential(&digits, decpt)
    } else {
        format_fixed(&digits, decpt)
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

/// `1e+22` / `1.5e-07`——指数永远带符号并补到两位（CPython 的 `%+.02d`）。
fn format_exponential(digits: &str, decpt: i32) -> String {
    let mut out = String::with_capacity(digits.len() + 6);
    out.push_str(&digits[..1]);
    if digits.len() > 1 {
        out.push('.');
        out.push_str(&digits[1..]);
    }
    out.push('e');
    let exp = decpt - 1;
    out.push(if exp < 0 { '-' } else { '+' });
    let mag = exp.unsigned_abs();
    if mag < 10 {
        out.push('0');
    }
    out.push_str(&mag.to_string());
    out
}

/// `0.0001` / `100.0`——整数形态补 `.0`（Python 的 repr 从不写成裸整数）。
fn format_fixed(digits: &str, decpt: i32) -> String {
    if decpt <= 0 {
        let mut out = String::from("0.");
        for _ in 0..(-decpt) {
            out.push('0');
        }
        out.push_str(digits);
        return out;
    }
    let decpt = decpt as usize;
    if decpt >= digits.len() {
        let mut out = String::from(digits);
        for _ in 0..(decpt - digits.len()) {
            out.push('0');
        }
        out.push_str(".0");
        return out;
    }
    format!("{}.{}", &digits[..decpt], &digits[decpt..])
}

#[cfg(test)]
mod tests {
    use super::format_f64;

    /// 左边是 Python `repr()` 的实际输出，一个字节都不许差。
    #[test]
    fn matches_python_repr() {
        let cases: &[(f64, &str)] = &[
            (0.0, "0.0"),
            (-0.0, "-0.0"),
            (1.0, "1.0"),
            (-1.0, "-1.0"),
            (2.5, "2.5"),
            (0.125, "0.125"),
            (0.1 + 0.2, "0.30000000000000004"),
            (1.0 / 3.0, "0.3333333333333333"),
            (100.0, "100.0"),
            (1230.0, "1230.0"),
            (0.0001, "0.0001"),
            (1e-5, "1e-05"),
            (1e-7, "1e-07"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1e22, "1e+22"),
            (1.5e22, "1.5e+22"),
            (5e-324, "5e-324"),
            (f64::MAX, "1.7976931348623157e+308"),
            (f64::MIN, "-1.7976931348623157e+308"),
            (-1e-7, "-1e-07"),
            (1e100, "1e+100"),
            (1e-100, "1e-100"),
        ];
        for (value, expected) in cases {
            assert_eq!(&format_f64(*value), expected, "repr({value:?})");
        }
    }

    /// 定点/指数的分界线正好卡在 CPython 的 decpt 判据上。
    #[test]
    fn exponential_threshold_matches_cpython() {
        assert_eq!(format_f64(1e16), "1e+16"); // decpt = 17 > 16
        assert_eq!(format_f64(9999999999999998.0), "9999999999999998.0"); // decpt = 16
        assert_eq!(format_f64(0.0001), "0.0001"); // decpt = -3
        assert_eq!(format_f64(0.00001), "1e-05"); // decpt = -4
    }

    /// 走一遍解析回来的往返：格式化过的字符串必须还是同一个 double。
    #[test]
    fn round_trips_through_parse() {
        let mut seed = 0x2545_F491_4F6C_DD1Du64;
        for _ in 0..20_000 {
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let v = f64::from_bits(seed);
            if !v.is_finite() {
                continue;
            }
            let text = format_f64(v);
            let back: f64 = text.parse().expect(&text);
            assert_eq!(back.to_bits(), v.to_bits(), "{text}");
        }
    }
}
