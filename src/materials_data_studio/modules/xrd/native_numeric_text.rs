use std::{io::{Cursor, Write}, slice, str};

#[no_mangle]
pub unsafe extern "C" fn mds_parse_numeric_cells(
    input: *const u8,
    length: usize,
    output: *mut f64,
    count: usize,
) -> bool {
    let input = slice::from_raw_parts(input, length);
    let output = slice::from_raw_parts_mut(output, count);
    let mut fields = input.split(|byte| *byte == 0);
    let mut normalized = Vec::new();
    for destination in output {
        let Some(field) = fields.next() else { return false };
        let field = if field.iter().any(|byte| matches!(byte, b'd' | b'D')) {
            normalized.clear();
            normalized.extend(field.iter().map(|byte| match byte {
                b'd' | b'D' => b'e',
                other => *other,
            }));
            &normalized[..]
        } else {
            field
        };
        *destination = str::from_utf8(field).ok()
            .and_then(|field| field.parse::<f64>().ok())
            .unwrap_or(f64::NAN);
    }
    fields.next().is_none()
}

fn write_float(writer: &mut impl Write, value: f64) -> std::io::Result<()> {
    // Rust's shortest representation round-trips to the same f64.
    if value.is_nan() {
        writer.write_all(b"nan")
    } else {
        write!(writer, "{value:?}")
    }
}

#[no_mangle]
pub unsafe extern "C" fn mds_write_numeric_rows(
    values: *const f64,
    count: usize,
    columns: usize,
    delimiter: u8,
    output: *mut u8,
    capacity: usize,
) -> usize {
    let values = slice::from_raw_parts(values, count);
    let mut writer = Cursor::new(slice::from_raw_parts_mut(output, capacity));
    for row in values.chunks_exact(columns) {
        for (column, &value) in row.iter().enumerate() {
            if column != 0 && writer.write_all(&[delimiter]).is_err() {
                return 0;
            }
            if write_float(&mut writer, value).is_err() {
                return 0;
            }
        }
        if writer.write_all(b"\n").is_err() {
            return 0;
        }
    }
    writer.position() as usize
}

#[repr(C)]
pub struct NumericColumn {
    data: *const u8,
    length: usize,
    kind: u8,
}

#[no_mangle]
pub unsafe extern "C" fn mds_write_numeric_columns(
    columns: *const NumericColumn,
    count: usize,
    rows: usize,
    delimiter: u8,
    crlf: bool,
    output: *mut u8,
    capacity: usize,
) -> usize {
    let columns = slice::from_raw_parts(columns, count);
    let mut writer = Cursor::new(slice::from_raw_parts_mut(output, capacity));
    for row in 0..rows {
        for (index, column) in columns.iter().enumerate() {
            if index != 0 && writer.write_all(&[delimiter]).is_err() {
                return 0;
            }
            if row >= column.length {
                continue;
            }
            let result = match column.kind {
                0 => writer.write_all(if *column.data.add(row) == 0 { b"False" } else { b"True" }),
                1 => write!(writer, "{}", *column.data.cast::<i64>().add(row)),
                2 => write!(writer, "{}", *column.data.cast::<u64>().add(row)),
                _ => write_float(&mut writer, *column.data.cast::<f64>().add(row)),
            };
            if result.is_err() {
                return 0;
            }
        }
        if writer.write_all(if crlf { b"\r\n" } else { b"\n" }).is_err() {
            return 0;
        }
    }
    writer.position() as usize
}

fn plain_number(cell: &[u8], fortran_exponents: bool, normalized: &mut Vec<u8>) -> Option<f64> {
    let cell = cell.trim_ascii();
    let cell = if fortran_exponents && cell.iter().any(|byte| matches!(byte, b'd' | b'D')) {
        normalized.clear();
        normalized.extend(cell.iter().map(|byte| match byte {
            b'd' | b'D' => b'e',
            other => *other,
        }));
        &normalized[..]
    } else {
        cell
    };
    str::from_utf8(cell).ok()?.parse().ok()
}

#[no_mangle]
pub unsafe extern "C" fn mds_parse_reference_numbers(
    input: *const u8,
    length: usize,
    output: *mut f64,
    capacity: usize,
) -> usize {
    let input = slice::from_raw_parts(input, length);
    let output = slice::from_raw_parts_mut(output, capacity);
    let mut count = 0;
    let mut normalized = Vec::new();
    for cell in input.split(|&byte| byte == b',') {
        let cell = cell.trim_ascii();
        if cell.is_empty() {
            continue;
        }
        if count == capacity {
            return usize::MAX;
        }
        let Some(value) = plain_number(cell, false, &mut normalized) else {
            return usize::MAX;
        };
        if !value.is_finite() {
            return usize::MAX;
        }
        output[count] = value;
        count += 1;
    }
    count
}

fn fields(row: &[u8], separator: u8) -> impl Iterator<Item = &[u8]> {
    row.split(move |byte| *byte == separator)
        .filter(move |cell| separator != b' ' || !cell.is_empty())
}

// Only plain, rectangular ASCII records are eligible. The general parser owns
// CSV quoting, embedded newlines, mixed/wide layouts and other encodings.
#[no_mangle]
pub unsafe extern "C" fn mds_index_numeric_rows(
    input: *const u8,
    length: usize,
    separator: u8,
    mode: u8,
    offsets: *mut u32,
    slots: usize,
    column_kinds: *mut u8,
    max_columns: usize,
    result: *mut u32,
    cancelled: extern "C" fn() -> bool,
) -> bool {
    let input = slice::from_raw_parts(input, length);
    let offsets = slice::from_raw_parts_mut(offsets, slots);
    let kinds = slice::from_raw_parts_mut(column_kinds, max_columns);
    let result = slice::from_raw_parts_mut(result, 2);
    let graph = mode == 0;
    let mut width = 0;
    let mut header = 0;
    let mut count = 0;
    let mut start = 0;
    let mut normalized = Vec::new();
    for raw in input.split_inclusive(|&byte| byte == b'\n') {
        if (count % 4096 == 0 && cancelled()) || count + 1 >= slots || raw.len() > 4 * 1024 * 1024 {
            return false;
        }
        let row = raw.strip_suffix(b"\n").unwrap_or(raw);
        let row = if raw.ends_with(b"\n") { row.strip_suffix(b"\r").unwrap_or(row) } else { row };
        if row.trim_ascii().is_empty() || row.iter().any(|&byte| !matches!(byte, b'\t' | b' '..=b'~') || byte == b'"' || (separator == b' ' && byte == b'\t')) {
            return false;
        }
        let mut cells = 0;
        let mut numeric = 0;
        for field in fields(row, separator) {
            if cells >= max_columns
                || (separator != b' ' && field.len() > 128 * 1024)
                || (graph && field.trim_ascii().is_empty())
                || (!graph && field.contains(&b'_')) {
                return false;
            }
            if !field.trim_ascii().is_empty() {
                if plain_number(field, graph, &mut normalized).is_some() {
                    numeric += 1;
                } else {
                    kinds[cells] = 0;
                }
            }
            cells += 1;
        }
        if count == 0 {
            width = cells;
            if graph && numeric == 0 {
                header = 1;
                kinds.fill(1);
            }
        }
        if cells < 2 || cells != width || (count + 1) * width > 5_000_000
            || (graph && numeric != cells && count >= header) {
            return false;
        }
        offsets[count] = start as u32;
        count += 1;
        start += raw.len();
    }
    if count <= header {
        return false;
    }
    offsets[count] = start as u32;
    result.copy_from_slice(&[width as u32, header as u32]);
    true
}

#[no_mangle]
pub unsafe extern "C" fn mds_extract_numeric_rows(
    input: *const u8,
    length: usize,
    offsets: *const u32,
    rows: usize,
    separator: u8,
    fortran_exponents: bool,
    columns: *const u32,
    selected: usize,
    stride: usize,
    row_offset: usize,
    output: *mut f64,
) {
    let input = slice::from_raw_parts(input, length);
    let offsets = slice::from_raw_parts(offsets, rows + 1);
    let columns = slice::from_raw_parts(columns, selected);
    let output = slice::from_raw_parts_mut(output, selected * stride);
    let mut normalized = Vec::new();
    for (row, bounds) in offsets.windows(2).enumerate() {
        let line = &input[bounds[0] as usize..bounds[1] as usize];
        let line = line.strip_suffix(b"\n").unwrap_or(line);
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        let mut col = columns.iter().enumerate().peekable();
        for (index, cell) in fields(line, separator).enumerate() {
            let Some(&(out_column, &source_column)) = col.peek() else { break };
            if index == source_column as usize {
                output[out_column * stride + row_offset + row] =
                    plain_number(cell, fortran_exponents, &mut normalized).unwrap_or(f64::NAN);
                col.next();
            }
        }
    }
}
