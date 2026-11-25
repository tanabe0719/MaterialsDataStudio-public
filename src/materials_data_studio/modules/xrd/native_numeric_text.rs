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
            // Rust's shortest representation round-trips to the same f64.
            let result = if value.is_nan() {
                writer.write_all(b"nan")
            } else {
                write!(writer, "{value:?}")
            };
            if result.is_err() {
                return 0;
            }
        }
        if writer.write_all(b"\n").is_err() {
            return 0;
        }
    }
    writer.position() as usize
}
