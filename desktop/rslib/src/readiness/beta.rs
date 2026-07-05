// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Self-contained Beta / Binomial math for the Readiness gauge (Anki
//! Speedrun, Phase 3 [R2]).
//!
//! Everything the gauge needs — the Jeffreys posterior over a pass rate,
//! its equal-tailed credible interval, and exact Binomial / Beta-Binomial
//! tails for the MPS map — implemented directly so `rslib` gains no stats
//! dependency. Accuracy is ~1e-10, far beyond what an n≤60 probe bank can
//! resolve; unit tests pin values cross-checked between two independent
//! implementations (continued fraction vs direct summation).

/// ln Γ(x) for x > 0 (Lanczos approximation, g=7, n=9).
pub(crate) fn ln_gamma(x: f64) -> f64 {
    const COEFFS: [f64; 8] = [
        676.5203681218851,
        -1259.1392167224028,
        771.323_428_777_653_1,
        -176.615_029_162_140_6,
        12.507343278686905,
        -0.13857109526572012,
        9.984_369_578_019_572e-6,
        1.5056327351493116e-7,
    ];
    debug_assert!(x > 0.0);
    if x < 0.5 {
        // reflection formula keeps small arguments accurate
        return std::f64::consts::PI.ln()
            - (std::f64::consts::PI * x).sin().ln()
            - ln_gamma(1.0 - x);
    }
    let x = x - 1.0;
    let mut acc = 0.999_999_999_999_809_9;
    for (i, c) in COEFFS.iter().enumerate() {
        acc += c / (x + (i as f64) + 1.0);
    }
    let t = x + 7.5;
    0.5 * (2.0 * std::f64::consts::PI).ln() + (x + 0.5) * t.ln() - t + acc.ln()
}

fn ln_beta(a: f64, b: f64) -> f64 {
    ln_gamma(a) + ln_gamma(b) - ln_gamma(a + b)
}

/// Regularized incomplete beta I_x(a, b) via the standard continued
/// fraction (Lentz's method), with the symmetry transform for convergence.
pub(crate) fn betainc(x: f64, a: f64, b: f64) -> f64 {
    debug_assert!((0.0..=1.0).contains(&x));
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let ln_front = a * x.ln() + b * (1.0 - x).ln() - ln_beta(a, b);
    if x < (a + 1.0) / (a + b + 2.0) {
        (ln_front.exp() * beta_cont_frac(x, a, b) / a).clamp(0.0, 1.0)
    } else {
        (1.0 - ln_front.exp() * beta_cont_frac(1.0 - x, b, a) / b).clamp(0.0, 1.0)
    }
}

fn beta_cont_frac(x: f64, a: f64, b: f64) -> f64 {
    const MAX_ITER: usize = 300;
    const EPS: f64 = 1e-14;
    const TINY: f64 = 1e-300;

    let qab = a + b;
    let qap = a + 1.0;
    let qam = a - 1.0;
    let mut c = 1.0;
    let mut d = 1.0 - qab * x / qap;
    if d.abs() < TINY {
        d = TINY;
    }
    d = 1.0 / d;
    let mut h = d;
    for m in 1..=MAX_ITER {
        let m = m as f64;
        let m2 = 2.0 * m;
        // even step
        let aa = m * (b - m) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        if d.abs() < TINY {
            d = TINY;
        }
        c = 1.0 + aa / c;
        if c.abs() < TINY {
            c = TINY;
        }
        d = 1.0 / d;
        h *= d * c;
        // odd step
        let aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        if d.abs() < TINY {
            d = TINY;
        }
        c = 1.0 + aa / c;
        if c.abs() < TINY {
            c = TINY;
        }
        d = 1.0 / d;
        let del = d * c;
        h *= del;
        if (del - 1.0).abs() < EPS {
            break;
        }
    }
    h
}

/// Quantile of the Beta(a, b) distribution by bisection on the CDF.
/// Plenty fast (≤60 iterations) and immune to the usual Newton blow-ups at
/// extreme quantiles of U-shaped (Jeffreys) posteriors.
pub(crate) fn beta_quantile(q: f64, a: f64, b: f64) -> f64 {
    debug_assert!((0.0..=1.0).contains(&q));
    let mut lo = 0.0f64;
    let mut hi = 1.0f64;
    for _ in 0..60 {
        let mid = 0.5 * (lo + hi);
        if betainc(mid, a, b) < q {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    0.5 * (lo + hi)
}

fn ln_choose(n: u32, k: u32) -> f64 {
    ln_gamma(n as f64 + 1.0) - ln_gamma(k as f64 + 1.0) - ln_gamma((n - k) as f64 + 1.0)
}

/// P(X ≥ k) for X ~ Binomial(n, p), by direct summation.
pub(crate) fn binomial_tail(n: u32, k: u32, p: f64) -> f64 {
    if k == 0 {
        return 1.0;
    }
    if k > n {
        return 0.0;
    }
    if p <= 0.0 {
        return 0.0;
    }
    if p >= 1.0 {
        return 1.0;
    }
    let (ln_p, ln_q) = (p.ln(), (1.0 - p).ln());
    let mut total = 0.0;
    for j in k..=n {
        total += (ln_choose(n, j) + (j as f64) * ln_p + ((n - j) as f64) * ln_q).exp();
    }
    total.clamp(0.0, 1.0)
}

/// P(X ≥ k) for X ~ Beta-Binomial(n, a, b) — the posterior predictive of
/// the exam score when the pass rate carries a Beta(a, b) posterior.
pub(crate) fn beta_binomial_tail(n: u32, k: u32, a: f64, b: f64) -> f64 {
    if k == 0 {
        return 1.0;
    }
    if k > n {
        return 0.0;
    }
    let ln_b_ab = ln_beta(a, b);
    let mut total = 0.0;
    for j in k..=n {
        total += (ln_choose(n, j) + ln_beta(a + j as f64, b + (n - j) as f64) - ln_b_ab).exp();
    }
    total.clamp(0.0, 1.0)
}

#[cfg(test)]
mod test {
    use super::*;

    fn close(a: f64, b: f64, tol: f64) {
        assert!((a - b).abs() < tol, "{a} vs {b}");
    }

    #[test]
    fn ln_gamma_matches_known_values() {
        close(ln_gamma(1.0), 0.0, 1e-12);
        close(ln_gamma(2.0), 0.0, 1e-12);
        close(ln_gamma(5.0), 24.0f64.ln(), 1e-10);
        // Γ(0.5) = √π
        close(ln_gamma(0.5), 0.5 * std::f64::consts::PI.ln(), 1e-10);
    }

    /// For integer a, b: I_x(a, b) = P(Bin(a+b-1, x) ≥ a). The two sides use
    /// independent implementations (continued fraction vs summation), so
    /// agreement pins both.
    #[test]
    fn betainc_agrees_with_binomial_identity() {
        for &(a, b) in &[(2u32, 3u32), (5, 5), (1, 9), (7, 2)] {
            for &x in &[0.1, 0.25, 0.4, 0.5, 0.65, 0.9] {
                let lhs = betainc(x, a as f64, b as f64);
                let rhs = binomial_tail(a + b - 1, a, x);
                close(lhs, rhs, 1e-10);
            }
        }
    }

    #[test]
    fn betainc_symmetric_case() {
        // Beta(0.5, 0.5) (the Jeffreys prior) is symmetric about 0.5
        close(betainc(0.5, 0.5, 0.5), 0.5, 1e-10);
        // uniform: I_x(1,1) = x
        close(betainc(0.37, 1.0, 1.0), 0.37, 1e-10);
    }

    #[test]
    fn quantiles_invert_the_cdf() {
        for &(a, b) in &[(0.5, 0.5), (8.5, 2.5), (25.5, 35.5)] {
            for &q in &[0.05, 0.5, 0.95] {
                let x = beta_quantile(q, a, b);
                close(betainc(x, a, b), q, 1e-9);
            }
        }
    }

    #[test]
    fn jeffreys_interval_for_8_of_10() {
        // x=8, n=10 → Beta(8.5, 2.5); the 90% equal-tailed (Jeffreys)
        // interval is ≈ [0.5475, 0.9398] (verified against an independent
        // Simpson-rule integration of the Beta pdf).
        let (a, b) = (8.5, 2.5);
        close(beta_quantile(0.05, a, b), 0.54750, 5e-5);
        close(beta_quantile(0.95, a, b), 0.93979, 5e-5);
    }

    #[test]
    fn binomial_tail_exact_values() {
        // P(Bin(10, 0.5) ≥ 5) = 638/1024
        close(binomial_tail(10, 5, 0.5), 0.623046875, 1e-12);
        assert_eq!(binomial_tail(10, 0, 0.1), 1.0);
        assert_eq!(binomial_tail(10, 11, 0.9), 0.0);
    }

    #[test]
    fn beta_binomial_reduces_to_binomial_at_high_concentration() {
        // Beta(a, b) with huge a+b and mean 0.7 ≈ point mass at 0.7
        let tail_bb = beta_binomial_tail(180, 126, 0.7e7, 0.3e7);
        let tail_b = binomial_tail(180, 126, 0.7);
        close(tail_bb, tail_b, 1e-3);
    }

    #[test]
    fn beta_binomial_prior_only_is_wide_open() {
        // With the bare Jeffreys prior the exam-score predictive is spread
        // out: P(score ≥ 70%) ≈ P(p ≥ 0.7 under arcsine) ≈ 0.37
        let tail = beta_binomial_tail(180, 126, 0.5, 0.5);
        assert!(tail > 0.3 && tail < 0.45, "{tail}");
    }
}
