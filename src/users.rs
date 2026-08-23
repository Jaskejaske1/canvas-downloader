use std::io::Write;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::Ordering;

use anyhow::{Context, Result};

use crate::api::get_pages;
use crate::canvas::ProcessOptions;
use crate::utils::{append_json_array_page, get_raw_json_path};

pub async fn process_users(
    (url, parent_path): (String, PathBuf),
    options: Arc<ProcessOptions>,
) -> Result<()> {
    if !options.save_json {
        return Ok(());
    }

    let users_url = format!(
        "{}users?include_inactive=true&include[]=avatar_url&include[]=enrollments&include[]=email&include[]=observed_users&include[]=can_be_removed&include[]=custom_links",
        url
    );
    let pages = get_pages(users_url, &options).await?;
    let mut raw_users = Vec::new();

    for page in pages {
        if let Err(error) = append_json_array_page(&mut raw_users, &page.body) {
            tracing::debug!(
                "Unable to preserve raw users from {} (access may be restricted): {error}",
                page.url
            );
            return Ok(());
        }
    }

    if let Some(users_path) = get_raw_json_path(
        &parent_path,
        "users.json",
        &options.base_path,
        options.save_json,
    )? {
        let users_path_str = users_path.to_string_lossy();
        let mut users_file = std::fs::File::create(users_path.clone())
            .with_context(|| format!("Unable to create file for {:?}", users_path_str))?;

        users_file
            .write_all(serde_json::to_string_pretty(&raw_users)?.as_bytes())
            .with_context(|| format!("Unable to write to file for {:?}", users_path_str))?;

        tracing::debug!(
            "👥 Users saved for {}",
            parent_path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
        );
        options.n_users.fetch_add(1, Ordering::Relaxed);
    }

    Ok(())
}
