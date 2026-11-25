use std::slice;

// Inputs come from contiguous, caller-owned float64 buffers via native_xrd.py.
// The diffraction kernel uses the same coefficient table, occupancy and
// Lorentz-polarization conventions as the reference implementation.
#[no_mangle]
pub unsafe extern "C" fn mds_diffraction(
    reflections: usize,
    hkl: *const f64,
    lengths: *const f64,
    atoms: usize,
    stride: usize,
    parameters: *const f64,
    wavelength: f64,
    angles: *mut f64,
    intensity: *mut f64,
) {
    let hkls = slice::from_raw_parts(hkl, reflections * 3);
    let lengths = slice::from_raw_parts(lengths, reflections);
    let parameters = slice::from_raw_parts(parameters, atoms * stride);
    let angles = slice::from_raw_parts_mut(angles, reflections);
    let intensity = slice::from_raw_parts_mut(intensity, reflections);
    for (i, (h, &length)) in hkls.chunks_exact(3).zip(lengths).enumerate() {
        let theta = (wavelength * length / 2.0).asin();
        let s2 = (length / 2.0).powi(2);
        let mut real = 0.0;
        let mut imaginary = 0.0;
        for atom in parameters.chunks_exact(stride) {
            let scattering = atom[0] - 41.78214 * s2 * atom[5..].chunks_exact(2)
                .map(|c| c[0] * (-c[1] * s2).exp()).sum::<f64>();
            let phase = 2.0 * std::f64::consts::PI *
                (h[0] * atom[1] + h[1] * atom[2] + h[2] * atom[3]);
            let (sin, cos) = phase.sin_cos();
            let amplitude = scattering * atom[4];
            real += amplitude * cos;
            imaginary += amplitude * sin;
        }
        let lorentz = (1.0 + (2.0 * theta).cos().powi(2)) /
            (theta.sin().powi(2) * theta.cos());
        angles[i] = (2.0 * theta).to_degrees();
        intensity[i] = (real * real + imaginary * imaginary) * lorentz;
    }
}

// Most small matching problems have disjoint stars: one observation may
// have several candidates or vice versa. Solve those directly. Return NaN
// for coupled choices/ties so SciPy keeps its reference assignment semantics.
#[no_mangle]
pub unsafe extern "C" fn mds_peak_score(
    observed: usize,
    x: *const f64,
    oi: *const f64,
    count: usize,
    ds: *const f64,
    intensities: *const f64,
    wavelength: f64,
    scale: f64,
    lower: f64,
    upper: f64,
    shift: f64,
    resolution: f64,
    tolerance: f64,
) -> f64 {
    let x = slice::from_raw_parts(x, observed);
    let oi = slice::from_raw_parts(oi, observed);
    let ds = slice::from_raw_parts(ds, count);
    let intensities = slice::from_raw_parts(intensities, count);
    let mut groups: Vec<(f64, f64, f64)> = Vec::with_capacity(count.min(4000));
    let mut previous = None;
    for (&d, &strength) in ds.iter().zip(intensities) {
        let arg = wavelength / (2.0 * d * scale);
        if strength <= 0.0 || arg <= 0.0 || arg >= 1.0 {
            continue;
        }
        let p = (2.0 * arg.asin()).to_degrees() + shift;
        if (p - lower).abs() < 1e-11 || (p - upper).abs() < 1e-11 {
            return f64::NAN;
        }
        if p < lower || p > upper {
            continue;
        }
        if previous.is_some_and(|last: f64| (last - p).abs() < 1e-12) {
            return f64::NAN;
        }
        previous = Some(p);
        if let Some(group) = groups.last_mut() {
            if ((p - group.2) - resolution).abs() < 1e-11 {
                return f64::NAN;
            }
            if p - group.2 <= resolution {
                let total = group.1 + strength;
                group.0 = (group.0 * group.1 + p * strength) / total;
                group.1 = total;
                continue;
            }
        }
        groups.push((p, strength, p));
    }
    if groups.is_empty() {
        return 0.0;
    }
    if groups.len() > 4000 || !oi.iter().any(|&v| v > 0.0) {
        return f64::NAN;
    }
    let max = groups.iter().map(|g| g.1).fold(0.0, f64::max);
    if !max.is_finite() || max <= 0.0 {
        return f64::NAN;
    }
    let mut total_pi = 0.0;
    for group in &mut groups {
        group.1 /= max;
        total_pi += group.1;
    }
    let mut best: Vec<Option<(usize, f64, f64)>> = vec![None; observed];
    let mut rows = vec![0; observed];
    let mut columns = vec![0; groups.len()];
    for (r, &position) in x.iter().enumerate() {
        let start = groups.partition_point(|g| g.0 < position - tolerance - 1e-11);
        for c in start..groups.len() {
            let delta = position - groups[c].0;
            if delta < -tolerance - 1e-11 {
                break;
            }
            if (delta.abs() - tolerance).abs() < 1e-11 {
                return f64::NAN;
            }
            let amplitude = (oi[r] * groups[c].1).sqrt();
            let agreement = (-0.5 * (delta / tolerance).powi(2)).exp();
            let weight = amplitude * agreement;
            if weight > 0.0 {
                rows[r] += 1;
                columns[c] += 1;
                match best[r] {
                    Some((_, old, _)) if weight == old => return f64::NAN,
                    Some((_, old, _)) if weight < old => {}
                    _ => best[r] = Some((c, weight, agreement)),
                }
            }
        }
    }
    let mut winners: Vec<Option<(usize, f64, f64)>> = vec![None; groups.len()];
    for (r, item) in best.into_iter().enumerate() {
        if rows[r] > 1 {
            let start = groups.partition_point(|g| g.0 < x[r] - tolerance - 1e-11);
            if (start..groups.len()).take_while(|&c| groups[c].0 <= x[r] + tolerance + 1e-11)
                .any(|c| columns[c] > 1) {
                return f64::NAN;
            }
        }
        if let Some((c, weight, agreement)) = item {
            match winners[c] {
                Some((_, old, _)) if weight == old => return f64::NAN,
                Some((_, old, _)) if weight < old => {}
                _ => winners[c] = Some((r, weight, agreement)),
            }
        }
    }
    let mut pair_sum = 0.0;
    let mut agreement_sum = 0.0;
    let mut covered_o = 0.0;
    let mut covered_p = 0.0;
    let mut a_sq = 0.0;
    let mut b_sq = 0.0;
    let mut dot = 0.0;
    for (c, pair) in winners.into_iter().enumerate() {
        if let Some((r, weight, agreement)) = pair {
            let a = oi[r];
            let b = groups[c].1;
            pair_sum += (a * b).sqrt();
            agreement_sum += weight;
            covered_o += a;
            covered_p += b;
            a_sq += a * a;
            b_sq += b * b;
            dot += a * b;
            debug_assert!(agreement > 0.0);
        }
    }
    if pair_sum == 0.0 {
        return 0.0;
    }
    let coverage = covered_o / oi.iter().sum::<f64>();
    let predicted = covered_p / total_pi;
    let cosine = dot / (a_sq.sqrt() * b_sq.sqrt());
    let intensity = cosine * (coverage * predicted).sqrt();
    100.0 * (0.35 * agreement_sum / pair_sum * predicted + 0.30 * coverage +
        0.20 * predicted + 0.15 * intensity)
}
