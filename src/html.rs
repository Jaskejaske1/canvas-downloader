use std::path::PathBuf;
use std::sync::Arc;

use anyhow::Result;
use futures::future::join_all;
use lazy_regex::regex;
use reqwest::Url;
use select::document::Document;
use select::predicate::Name;

use crate::canvas::{File, ProcessOptions};
use crate::files::{filter_files, prepare_link_for_download, process_file_id};
use crate::utils::create_folder_if_not_exist_or_ignored;

fn stable_url_key(raw_url: &str) -> String {
    Url::parse(raw_url)
        .map(|mut url| {
            url.set_query(None);
            url.set_fragment(None);
            url.to_string()
        })
        .unwrap_or_else(|_| {
            raw_url
                .split(['?', '#'])
                .next()
                .unwrap_or(raw_url)
                .to_string()
        })
}

fn stable_hash(value: &str) -> u64 {
    // FNV-1a is deliberately simple and, unlike DefaultHasher, has a stable
    // algorithm across Rust releases. This is an output key, not a security
    // boundary.
    value
        .as_bytes()
        .iter()
        .fold(0xcbf29ce484222325, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
        })
}

fn prefix_idless_file_name(file: &mut File) {
    if file.id == 0 {
        file.display_name = format!(
            "{:016x}_{}",
            stable_hash(&stable_url_key(&file.url)),
            file.display_name
        );
    }
}

/// process_html_links processes HTML content to find links and add them to the download queue.
/// will create a folder of the given folder_name under path if there are any files to download.
pub async fn process_html_links(
    (html, path, folder_name): (String, PathBuf, String),
    options: Arc<ProcessOptions>,
) -> Result<()> {
    let destination_path = path.join(sanitize_filename::sanitize(&folder_name));
    // If file link is part of course files
    let re = regex!(r"/courses/[0-9]+/files/([0-9]+)");
    let file_links = Document::from(html.as_str())
        .find(Name("a"))
        .filter_map(|n| n.attr("href"))
        .filter(|x| x.starts_with(&options.canvas_url))
        .filter_map(|x| Url::parse(x).ok())
        .filter(|x| re.is_match(x.path()))
        .filter_map(|x| {
            // Extract file ID and use the correct Canvas API endpoint
            re.captures(x.path())
                .and_then(|cap| cap.get(1))
                .map(|file_id| format!("{}/api/v1/files/{}", options.canvas_url, file_id.as_str()))
        })
        .collect::<Vec<String>>();

    let mut link_files = join_all(
        file_links
            .into_iter()
            .map(|x| process_file_id((x, destination_path.clone()), options.clone())),
    )
    .await
    .into_iter()
    .filter_map(|result| match result {
        Ok(file) => Some(file),
        Err(error) => {
            tracing::warn!("Skipping embedded Canvas file: {error:#}");
            None
        }
    })
    .map(|mut file| {
        prefix_idless_file_name(&mut file);
        file
    })
    .collect::<Vec<File>>();

    // If image is from canvas it is likely the file url gives permission denied, so download from the CDN
    let image_links = Document::from(html.as_str())
        .find(Name("img"))
        .filter_map(|n| n.attr("src"))
        .filter(|x| x.starts_with(&options.canvas_url))
        .filter(|x| !x.contains("equation_images"))
        .map(|x| x.to_string())
        .collect::<Vec<String>>();

    link_files.append(
        join_all(
            image_links
                .into_iter()
                .map(|x| prepare_link_for_download((x, destination_path.clone()), options.clone())),
        )
        .await
        .into_iter()
        .filter_map(|result| match result {
            Ok(file) => Some(file),
            Err(error) => {
                tracing::warn!("Skipping embedded image: {error:#}");
                None
            }
        })
        .map(|mut file| {
            prefix_idless_file_name(&mut file);
            file
        })
        .collect::<Vec<File>>()
        .as_mut(),
    );

    let mut filtered_files = filter_files(&options, &destination_path, link_files);

    if !filtered_files.is_empty() {
        // create folder if there are files to download
        create_folder_if_not_exist_or_ignored(&destination_path, &options)?;

        let mut lock = options.files_to_download.lock().await;
        lock.append(&mut filtered_files);
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{stable_hash, stable_url_key};

    #[test]
    fn url_output_keys_include_origin_but_ignore_volatile_query_data() {
        let first = stable_url_key("https://a.example/image.png?signature=one#preview");
        let refreshed = stable_url_key("https://a.example/image.png?signature=two");
        let other_origin = stable_url_key("https://b.example/image.png?signature=one");

        assert_eq!(first, refreshed);
        assert_ne!(stable_hash(&first), stable_hash(&other_origin));
    }
}
