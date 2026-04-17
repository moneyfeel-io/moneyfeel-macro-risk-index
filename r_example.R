# MoneyFeel MRI API — R Example
# =================================
# Full example: fetch regime history and timeseries.
#
# Requirements:
#   install.packages(c("httr2", "dplyr", "readr"))
#
# Get your free API key at: https://moneyfeel.it/account

library(httr2)
library(dplyr)
library(readr)

API_KEY <- "mf_live_YOUR_KEY"
BASE    <- "https://api.moneyfeel.ai/v1"

# Helper: authenticated request
mri_request <- function(endpoint, params = list()) {
  request(BASE) |>
    req_url_path_append(endpoint) |>
    req_url_query(!!!params) |>
    req_headers(Authorization = paste("Bearer", API_KEY)) |>
    req_error(is_error = \(r) FALSE) |>
    req_perform()
}

# ── 1. Current regime (no auth) ────────────────────────────────────────────

get_current <- function() {
  resp <- request(paste0(BASE, "/current")) |>
    req_perform()
  as.data.frame(do.call(rbind, lapply(resp_body_json(resp)$data, as.data.frame)))
}

# ── 2. Historical regime data ──────────────────────────────────────────────

get_history <- function(region = "US", tf = "WEEKLY", from = "2020-01-01") {
  resp <- mri_request("history", list(region = region, tf = tf, from = from))
  rows <- resp_body_json(resp)$data
  as.data.frame(do.call(rbind, lapply(rows, function(r) {
    r[sapply(r, is.null)] <- NA
    as.data.frame(r)
  })))
}

# ── 3. Strategy timeseries ─────────────────────────────────────────────────

get_timeseries <- function(region = "US", tf = "WEEKLY", from = "2020-01-01") {
  resp <- mri_request("timeseries", list(region = region, tf = tf, from = from))
  rows <- resp_body_json(resp)$data
  as.data.frame(do.call(rbind, lapply(rows, function(r) {
    r[sapply(r, is.null)] <- NA
    as.data.frame(r)
  })))
}

# ── 4. Download full CSV ───────────────────────────────────────────────────

download_csv <- function(region = "US", tf = "WEEKLY", output_file = NULL) {
  resp <- mri_request("download", list(region = region, tf = tf))
  csv_text <- resp_body_string(resp)

  if (!is.null(output_file)) {
    writeLines(csv_text, output_file)
    message("Saved to ", output_file)
    return(invisible(output_file))
  }

  # Parse inline (skip comment lines starting with #)
  lines <- strsplit(csv_text, "\n")[[1]]
  data_lines <- lines[!startsWith(lines, "#")]
  read_csv(paste(data_lines, collapse = "\n"), show_col_types = FALSE)
}

# ── 5. Performance metrics ─────────────────────────────────────────────────

get_metrics <- function(region = "US", tf = "WEEKLY") {
  resp <- mri_request("metrics", list(region = region, tf = tf))
  rows <- resp_body_json(resp)$data
  if (length(rows) == 0) return(NULL)
  as.data.frame(rows[[1]])
}


# ── Example usage ──────────────────────────────────────────────────────────

# Current regime
current <- get_current()
cat("=== Current Regime ===\n")
print(current[, c("region", "regime_weekly", "score_weekly")])

# US Weekly history
cat("\n=== US Weekly History (2024+) ===\n")
history <- get_history("US", "WEEKLY", "2024-01-01")
print(tail(history[, c("as_of_date", "regime", "mri_score")], 5))

# Metrics
cat("\n=== US Weekly Metrics ===\n")
metrics <- get_metrics("US", "WEEKLY")
cat("CAGR Overlay: ", metrics$cagr_strategy, "%\n")
cat("Sharpe Ratio: ", metrics$sharpe, "\n")
cat("Max Drawdown: ", metrics$max_drawdown, "%\n")

# Full CSV download
cat("\n=== Downloading full dataset ===\n")
df <- download_csv("US", "WEEKLY")
cat("Rows:", nrow(df), "| Columns:", ncol(df), "\n")
cat("Date range:", min(df$as_of_date), "→", max(df$as_of_date), "\n")
