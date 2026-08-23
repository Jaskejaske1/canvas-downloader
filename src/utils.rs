use crate::canvas::{Course, ProcessOptions};
use anyhow::{Context, Result};
use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub fn sanitize_path_component(name: &str) -> String {
    sanitize_filename::sanitize_with_options(
        name,
        sanitize_filename::Options {
            // Use the Windows-safe superset on every host so downloads have
            // portable, predictable directory names.
            windows: true,
            replacement: "_",
            ..Default::default()
        },
    )
}

pub fn output_name_with_id(id: u64, name: &str) -> String {
    sanitize_path_component(&format!("{id}_{name}"))
}

pub fn output_directory_name_with_id(id: u64, name: &str) -> String {
    sanitize_path_component(&format!("d{id}_{name}"))
}

pub fn output_name_with_string_id(id: &str, name: &str) -> String {
    let id = sanitize_path_component(id);
    sanitize_path_component(&format!("{id}_{name}"))
}

pub fn output_directory_name_with_string_id(id: &str, name: &str) -> String {
    let id = sanitize_path_component(id);
    sanitize_path_component(&format!("d{id}_{name}"))
}

pub fn print_all_courses_by_term(courses: &[Course]) {
    let mut grouped_courses: HashMap<u64, Vec<(&str, &str)>> = HashMap::new();

    for course in courses.iter() {
        let course_id: u64 = course.enrollment_term_id;
        grouped_courses
            .entry(course_id)
            .or_default()
            .push((&course.course_code, &course.name));
    }

    // Calculate column widths
    let max_code_width = courses
        .iter()
        .map(|c| c.course_code.len())
        .max()
        .unwrap_or(12)
        .max(12); // At least 12 for "Course Code" header

    // Print header
    println!(
        "{:<10} | {:<width$} | Course Name",
        "Term ID",
        "Course Code",
        width = max_code_width
    );
    println!("{}", "-".repeat(10 + 3 + max_code_width + 3 + 40));

    // Sort by term ID for consistent output
    let mut term_ids: Vec<_> = grouped_courses.keys().collect();
    term_ids.sort();

    for (term_idx, term_id) in term_ids.iter().enumerate() {
        let courses_in_term = &grouped_courses[term_id];
        for (i, (code, name)) in courses_in_term.iter().enumerate() {
            if i == 0 {
                println!(
                    "{:<10} | {:<width$} | {}",
                    term_id,
                    code,
                    name,
                    width = max_code_width
                );
            } else {
                println!(
                    "{:<10} | {:<width$} | {}",
                    "",
                    code,
                    name,
                    width = max_code_width
                );
            }
        }

        // Add separator line between terms (but not after the last one)
        if term_idx < term_ids.len() - 1 {
            println!("{}", "-".repeat(10 + 3 + max_code_width + 3 + 40));
        }
    }
}

pub fn ignored(
    filepath: &Path,
    is_dir: bool,
    base_path: &Path,
    ignore_matcher: Option<&ignore::gitignore::Gitignore>,
) -> bool {
    let matcher = match ignore_matcher {
        Some(m) => m,
        None => return false,
    };

    let relative_path = filepath.strip_prefix(base_path).unwrap_or(filepath);
    let ignored = matcher
        .matched_path_or_any_parents(relative_path, is_dir)
        .is_ignore();
    if ignored {
        tracing::debug!("Ignoring path: {}", filepath.display());
    }
    ignored
}

fn create_folder_if_not_exist(folder_path: &Path) -> Result<()> {
    std::fs::create_dir_all(folder_path).with_context(|| {
        format!(
            "Failed to create directory: {}",
            folder_path.to_string_lossy()
        )
    })?;
    Ok(())
}

// return Ok(true) if folder created or already exists, Ok(false) if ignored
pub fn create_folder_if_not_exist_or_ignored(
    folder_path: &Path,
    options: &ProcessOptions,
) -> Result<bool> {
    if ignored(
        folder_path,
        true,
        &options.base_path,
        options.ignore_matcher.as_deref(),
    ) {
        return Ok(false);
    }

    create_folder_if_not_exist(folder_path)?;
    Ok(true)
}

pub fn prettify_json(json_str: &str) -> Result<String> {
    let value: Value = serde_json::from_str(json_str)?;
    Ok(serde_json::to_string_pretty(&value)?)
}

pub fn append_json_array_page(
    aggregate: &mut Vec<serde_json::Value>,
    body: &str,
) -> serde_json::Result<()> {
    let mut page = serde_json::from_str::<Vec<serde_json::Value>>(body)?;
    aggregate.append(&mut page);
    Ok(())
}

/// Get the path for a raw JSON file in a parallel "raw" folder structure
/// Returns None if save_json is false
///
/// Example: if current_path is "/downloads/course1/assignments/Assignment 1"
/// and base_download_path is "/downloads", the raw path will be
/// "/downloads/raw/course1/assignments/Assignment 1/{filename}"
pub fn get_raw_json_path(
    current_path: &Path,
    filename: &str,
    base_path: &Path,
    save_json: bool,
) -> Result<Option<PathBuf>> {
    if !save_json {
        return Ok(None);
    }

    // Calculate relative path from base to current location
    let relative_path = current_path.strip_prefix(base_path).unwrap_or(current_path);

    // Create the mirrored structure in parallel "raw" folder
    let raw_path = base_path.join("raw").join(relative_path);

    create_folder_if_not_exist(&raw_path)?;
    Ok(Some(raw_path.join(filename)))
}

pub fn format_bytes(bytes: u64) -> String {
    const UNITS: [&str; 6] = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];

    if bytes == 0 {
        return "0 B".to_string();
    }

    let bytes_f64 = bytes as f64;
    // For 1024-based: exponent = floor(log2(bytes) / 10) = floor(log2(bytes) / log2(1024))
    let exponent = (bytes_f64.log2() / 10.0).floor() as usize;
    let exponent = exponent.min(UNITS.len() - 1);

    let size = bytes_f64 / 1024_f64.powi(exponent as i32);
    let unit = UNITS[exponent];

    if size >= 100.0 {
        format!("{:.0} {}", size, unit)
    } else if size >= 10.0 {
        format!("{:.1} {}", size, unit)
    } else {
        format!("{:.2} {}", size, unit)
    }
}

#[cfg(test)]
mod tests {
    use super::{
        append_json_array_page, output_directory_name_with_id,
        output_directory_name_with_string_id, output_name_with_id, output_name_with_string_id,
        sanitize_path_component,
    };

    #[test]
    fn path_components_are_sanitized_consistently_across_platforms() {
        assert_eq!(
            sanitize_path_component("Course Name: Course/Name 2"),
            "Course Name_ Course_Name 2"
        );
        assert_eq!(sanitize_path_component("CON"), "_");
        assert_eq!(sanitize_path_component("Course. "), "Course_");
    }

    #[test]
    fn stable_ids_disambiguate_sanitization_equivalent_names() {
        assert_ne!(
            output_name_with_id(17, "BIO/101"),
            output_name_with_id(18, "BIO101")
        );
        assert_eq!(
            output_directory_name_with_id(23, "Week/One"),
            "d23_Week_One"
        );
        assert_eq!(
            output_name_with_string_id("delivery/17", "Lecture/One"),
            "delivery_17_Lecture_One"
        );
        assert_eq!(
            output_directory_name_with_string_id("folder/17", "Lectures/2026"),
            "dfolder_17_Lectures_2026"
        );
    }

    #[test]
    fn json_array_pages_preserve_order_and_unknown_fields() {
        let mut aggregate = Vec::new();
        append_json_array_page(
            &mut aggregate,
            r#"[{"id":1,"unknown":{"kept":true}},{"id":2}]"#,
        )
        .expect("first page");
        append_json_array_page(&mut aggregate, r#"[{"id":3}]"#).expect("second page");

        assert_eq!(
            aggregate
                .iter()
                .map(|item| item["id"].as_u64())
                .collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );
        assert_eq!(aggregate[0]["unknown"]["kept"], true);
    }

    #[test]
    fn json_array_pages_reject_non_arrays() {
        assert!(append_json_array_page(&mut Vec::new(), r#"{"id":1}"#).is_err());
    }
}
