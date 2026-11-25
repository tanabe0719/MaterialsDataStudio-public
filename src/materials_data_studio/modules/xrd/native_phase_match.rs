use std::slice;

#[derive(Clone, Copy)]
struct Choice {
    index: usize,
    weight: f64,
    ambiguous: bool,
}

fn consider(choice: &mut Choice, index: usize, weight: f64) {
    let close = (weight - choice.weight).abs() <= 1e-12 * weight.max(choice.weight);
    if weight > choice.weight {
        *choice = Choice { index, weight, ambiguous: close };
    } else if close {
        choice.ambiguous = true;
    }
}

// Write columns for simple star-shaped components. Coupled choices and close
// ties go through the existing SciPy assignment, including its tie handling.
#[no_mangle]
pub unsafe extern "C" fn mds_peak_pairs(
    rows: usize,
    positions: *const f64,
    observed: *const f64,
    columns: usize,
    centers: *const f64,
    predicted: *const f64,
    tolerance: f64,
    pairs: *mut i32,
) -> bool {
    let positions = slice::from_raw_parts(positions, rows);
    let observed = slice::from_raw_parts(observed, rows);
    let centers = slice::from_raw_parts(centers, columns);
    let predicted = slice::from_raw_parts(predicted, columns);
    let pairs = slice::from_raw_parts_mut(pairs, rows);

    let empty = Choice { index: 0, weight: 0.0, ambiguous: false };
    let mut column_choices = vec![empty; columns];
    let mut column_counts = vec![0; columns];
    let mut row_choices = vec![empty; rows];
    let mut multiple = Vec::new();
    for (row, (&position, &intensity)) in positions.iter().zip(observed).enumerate() {
        if intensity == 0.0 {
            continue;
        }
        let start = centers.partition_point(|&center| position - center > tolerance);
        let mut eligible = Vec::new();
        for column in start..columns {
            let delta = position - centers[column];
            if delta < -tolerance {
                break;
            }
            let weight = (intensity * predicted[column]).sqrt()
                * (-0.5 * (delta / tolerance).powi(2)).exp();
            if weight <= 0.0 {
                continue;
            }
            consider(&mut row_choices[row], column, weight);
            consider(&mut column_choices[column], row, weight);
            column_counts[column] += 1;
            eligible.push(column);
        }
        if eligible.len() > 1 {
            multiple.extend(eligible);
        }
    }
    if multiple.iter().any(|&column| column_counts[column] > 1)
        || row_choices.iter().any(|choice| choice.ambiguous)
        || column_choices.iter().any(|choice| choice.ambiguous) {
        return false;
    }
    for (row, (slot, choice)) in pairs.iter_mut().zip(row_choices).enumerate() {
        *slot = if choice.weight > 0.0 && column_choices[choice.index].index == row {
            choice.index as i32
        } else {
            -1
        };
    }
    true
}
