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

/// process_html_links processes HTML content to find links and add them to the download queue.
/// will create a folder of the given folder_name under path if there are any files to download.
pub async fn process_html_links(
    (html, path, folder_name): (String, PathBuf, String),
    options: Arc<ProcessOptions>,
) -> Result<()> {
    let destination_path = path.join(sanitize_filename::sanitize(&folder_name));

    // Canvas course files can appear as normal links or as image preview URLs.
    // Resolve both through the File API so we get the canonical filename,
    // metadata and download URL.
    let re = regex!(r"/courses/[0-9]+/files/([0-9]+)");

    let mut file_links = Document::from(html.as_str())
        .find(Name("a"))
        .filter_map(|n| n.attr("href"))
        .filter(|x| x.starts_with(&options.canvas_url))
        .filter_map(|x| Url::parse(x).ok())
        .filter_map(|x| {
            re.captures(x.path())
                .and_then(|cap| cap.get(1))
                .map(|file_id| format!("{}/api/v1/files/{}", options.canvas_url, file_id.as_str()))
        })
        .collect::<Vec<String>>();

    file_links.extend(
        Document::from(html.as_str())
            .find(Name("img"))
            .filter_map(|n| n.attr("src"))
            .filter(|x| x.starts_with(&options.canvas_url))
            .filter_map(|x| Url::parse(x).ok())
            .filter_map(|x| {
                re.captures(x.path())
                    .and_then(|cap| cap.get(1))
                    .map(|file_id| {
                        format!("{}/api/v1/files/{}", options.canvas_url, file_id.as_str())
                    })
            }),
    );

    // The same Canvas file can be referenced multiple times in one HTML body.
    // Queue each file ID only once to avoid concurrent writes to the same path.
    file_links.sort_unstable();
    file_links.dedup();

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
    .collect::<Vec<File>>();

    // Other Canvas-hosted images that are not course File objects are downloaded
    // from their original URL.
    let mut image_links = Document::from(html.as_str())
        .find(Name("img"))
        .filter_map(|n| n.attr("src"))
        .filter(|x| x.starts_with(&options.canvas_url))
        .filter(|x| !x.contains("equation_images"))
        .filter_map(|x| Url::parse(x).ok())
        .filter(|x| !re.is_match(x.path()))
        .map(|x| x.to_string())
        .collect::<Vec<String>>();

    image_links.sort_unstable();
    image_links.dedup();

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
