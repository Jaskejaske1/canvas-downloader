use crate::canvas::ProcessOptions;
use anyhow::{Context, Error, Result};
use rand::Rng;
use reqwest::{Response, header};
use std::time::Duration;

const CANVAS_API_TIMEOUT: Duration = Duration::from_secs(60);
const CANVAS_API_RETRIES: u32 = 3;

pub struct ApiPage {
    pub url: String,
    pub body: String,
}

pub async fn get_pages(link: String, options: &ProcessOptions) -> Result<Vec<ApiPage>> {
    fn parse_next_page(resp: &Response) -> Result<Option<String>> {
        // Parse LINK header
        let Some(links) = resp
            .headers()
            .get(header::LINK)
            .and_then(|v| v.to_str().ok())
        else {
            return Ok(None);
        };
        let rels = parse_link_header::parse_with_rel(links).context(format!(
            "Error parsing pagination Link header for {}",
            resp.url()
        ))?;

        // Is last page?
        Ok(rels.get("next").map(|nex| nex.raw_uri.clone()))
    }

    let mut link = Some(link);
    let mut pages = Vec::new();

    while let Some(uri) = link {
        // GET request
        let resp = get_canvas_api(uri, options).await?;

        // Read the pagination header before consuming the response, then read
        // the body immediately. Holding several unconsumed responses can make
        // an earlier request hit its total timeout while later pages load.
        link = parse_next_page(&resp)?;
        let url = resp.url().to_string();
        let body = resp
            .text()
            .await
            .with_context(|| format!("Unable to read paginated Canvas response from {url}"))?;
        pages.push(ApiPage { url, body });
    }

    Ok(pages)
}

pub async fn get_canvas_api(url: String, options: &ProcessOptions) -> Result<Response> {
    for retry in 0..CANVAS_API_RETRIES {
        let resp = options
            .client
            .get(&url)
            .bearer_auth(&options.canvas_token)
            .timeout(CANVAS_API_TIMEOUT)
            .send()
            .await;

        match resp {
            Ok(resp) => {
                let status = resp.status();
                if status == reqwest::StatusCode::FORBIDDEN
                    || status == reqwest::StatusCode::TOO_MANY_REQUESTS
                    || status.is_server_error()
                {
                    if retry + 1 == CANVAS_API_RETRIES {
                        // Log more specific error information on final retry
                        if url.contains("users") {
                            tracing::debug!(
                                "Access denied to user data for course - API token may need elevated permissions"
                            );
                        } else if url.contains("discussion_topics") {
                            tracing::debug!(
                                "Access denied to discussions - course may have restricted discussion access"
                            );
                        } else {
                            tracing::debug!(
                                "Access denied to {} - check API token permissions",
                                url
                            );
                        }
                        return Ok(resp);
                    }
                } else {
                    return Ok(resp);
                }
            }
            Err(e) => {
                if retry + 1 == CANVAS_API_RETRIES {
                    tracing::error!("Canvas request error uri: {} {}", url, e);
                    return Err(e)
                        .with_context(|| format!("Canvas request failed after retries: {url}"));
                }
            }
        }

        // Exponential backoff with jitter: base delay * 2^retry + random jitter
        let base_delay = 500; // 500ms base delay
        let exponential_delay = base_delay * 2_u64.pow(retry);
        let jitter = rand::rng().random_range(0..=exponential_delay / 2);
        let wait_time = Duration::from_millis(exponential_delay + jitter);

        tracing::debug!(
            "Retrying Canvas request for {}, waiting {:?} before retry {}/{}",
            url,
            wait_time,
            retry + 1,
            CANVAS_API_RETRIES
        );
        tokio::time::sleep(wait_time).await;
    }
    Err(Error::msg("canvas request failed"))
}
