# Canvas Downloader

A command-line tool to download and organize all your Canvas course materials—files, syllabi, pages, modules, assignments, discussions, and announcements—into a clean local folder structure. Made in async Rust⚡.

This is a hardened fork of [bnjmnt4n/canvas-downloader](https://github.com/bnjmnt4n/canvas-downloader) with critical stability improvements for Windows and long path safety.

## Stability Improvements

This fork includes battle-tested fixes that make the tool more robust, especially on Windows:

- **Windows `MAX_PATH` Safe:** Intelligently flattens redundant double-folder nesting to avoid hitting path length limits.
- **Universal Path Sanitization:** Safely sanitizes all module, folder, and item names used in path construction.
- **Ghost File Prevention:** Actively skips hidden "preview" files before any HTTP request is made, preventing `401 Unauthorized` log spam.
- **Graceful Failure:** Cleanly handles aborted downloads without leaving `.tmp` files or throwing `NotFound` cleanup errors.

## Installation

#### ⬇️ Download from Releases (All platforms)

Download the corresponding binary archive from [Releases](https://github.com/Jaskejaske1/canvas-downloader/releases), decompress the archive, and run the executable directly or move it to `$PATH`.

#### 🛠️ Build from Source (All platforms)

```bash
git clone https://github.com/Jaskejaske1/canvas-downloader.git
cd canvas-downloader
cargo build --release
```

The compiled binary will be at `target/release/canvas-downloader` (or `target/release/canvas-downloader.exe` on Windows).

For macOS, you may need to remove the quarantine attribute before running:

```bash
xattr -d com.apple.quarantine canvas-downloader
```

## Quick Start

### 1. Create Configuration File

Copy the [example config](examples/config.toml) into one of the **config file locations (searched in order):**

1. Custom path via `--config` option
1. `canvas-downloader.toml` in current directory
1. `config.toml` in platform-specific config directory:
   - Linux: `~/.config/canvas-downloader/config.toml`
   - macOS: `~/.config/canvas-downloader/config.toml` or `~/Library/Application Support/canvas-downloader/config.toml`
   - Windows: `%APPDATA%\canvas-downloader\config.toml`

Then modify it with your Canvas instance URL and access token.

#### How to get your token

- Log in to Canvas → Account → Settings → **New Access Token**

### 2. Discover Your Courses

Run the tool to see which courses are available:

```shell
$ canvas-downloader
Please provide either Term ID(s) via -t or course name(s)/code(s) via -c
Term ID    | Course Code | Course Name
-----------------------------------------------------------
115        | CS1101S     | Programming Methodology
           | CS1231S     | Discrete Structures
-----------------------------------------------------------
120        | CS2040S     | Data Structures and Algorithms
           | CS2030      | Programming Methodology II
-----------------------------------------------------------
125        | CS3230      | Design and Analysis of Algorithms
```

### 3. Download Your Courses

You can download courses by term ID or by course name/code:

**Download by terms (all courses in specific terms):**

```shell
$ canvas-downloader -t 115 120
```

**Download by course names and/or codes (specific courses only):**

```shell
$ canvas-downloader -c CS1101S "Introduction to Data Structures"
```

**Combine both (courses matching both criteria):**

```shell
$ canvas-downloader -t 115 -c CS1101S
```

The tool will show you all files to be downloaded with their sizes, then ask for confirmation before proceeding. Downloads are organized by course, preserving Canvas's folder structure.

> **Note:** Course name matching is exact match — use the exact course code (e.g., "CS1101S") or the exact course name as shown in the discovery step.

## What Gets Downloaded

- [x] Files
- [x] Modules
- [x] Syllabi (in HTML and JSON)
- [x] Assignments (in HTML and JSON)
- [x] Discussions and announcements (in HTML and JSON)
- [x] Pages (in HTML and JSON)
- [x] User information (in JSON)
- [ ] Panopto lecture videos (experimental)

Paths are cleanly flattened — redundant nesting (e.g. `.../L6 LCD/L6 LCD/...`) is automatically avoided, making the download structure more readable and helping on Windows systems with `MAX_PATH` limits.

## Common Workflows

### Filter What You Download

Create a `.canvasignore` file in your current directory to skip certain files using `.gitignore` syntax:

```shell
# Ignore all videos
*.mp4
*.mov

# Ignore specific courses
/CS1101S/

# Ignore lecture recordings folder
lecture-recordings/
```

The tool automatically loads `.canvasignore` from the current directory if it exists. You can also specify a custom ignore file with `-i`:

```shell
$ canvas-downloader -t 115 -i custom-ignore.txt
```

See the [example file](examples/.canvasignore) for more patterns.

### Keep Your Files Updated

Use `-n` to overwrite local files with newer versions from Canvas:

```shell
$ canvas-downloader -t 115 -n
```

By default, existing local files won't be overwritten even if Canvas has newer versions.

### Choose Download Location

Specify a custom folder with `-d`:

```shell
$ canvas-downloader -t 115 -d ~/Canvas
```

### See Debug Information

Use `-v` to enable verbose output for troubleshooting:

```shell
# Enable debug logging
$ canvas-downloader -t 115 -v
```

Without `-v`, only important progress messages are shown (info level).

## All Options

```
Usage: canvas-downloader [OPTIONS]

Options:
      --config <FILE>                Path to config file (default: platform-specific config locations)
  -d, --destination-folder <FOLDER>  Download location [default: .]
  -n, --download-newer               Overwrite local files with newer Canvas versions
  -t, --term-ids <ID>...             Term IDs to download
  -c, --course-names <NAME>...       Course names or codes to download - exact match
  -i, --ignore-file <FILE>           Path to ignore patterns file [default: .canvasignore]
      --dry-run                      Preview downloads without executing
      --no-raw                       Do not save raw JSON responses
      --no-submissions               Do not download assignment submission files
  -v, --verbose                      Enable debug logging
  -h, --help                         Print help
  -V, --version                      Print version
```
