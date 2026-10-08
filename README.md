# Canvas Downloader

A command-line tool to download and organize all your Canvas course materials—files, syllabi, pages, modules, assignments, discussions, and announcements—into a clean local folder structure. Made in async Rust⚡.

This is a maintained fork of [this project](https://github.com/bnjmnt4n/canvas-downloader). Also shout out to [this previous fork](https://github.com/rhgndf/canvas-downloader) that implements downloading additional materials.

## Installation

#### 🍺 Homebrew (macOS/Linux)

```bash
brew install aik2mlj/tap/canvas-downloader
```

#### 📦 AUR (Arch Linux)

```bash
# use pre-built binary
paru -S canvas-downloader-bin

# or compile from source
paru -S canvas-downloader
```

#### 🍦 Scoop (Windows)

```powershell
scoop bucket add aik2mlj https://github.com/aik2mlj/scoop-bucket
scoop install aik2mlj/canvas-downloader
```

#### 🛠️ Cargo (All platforms)

```bash
# use pre-built binary
# you need to have cargo-binstall installed first
cargo binstall canvas-downloader

# or compile from source
cargo install canvas-downloader
```

Windows source builds may also require CMake and NASM because of native TLS dependencies.

#### ⬇️ Download from Releases (All platforms)

- Download the corresponding binary archive from [Releases](https://github.com/aik2mlj/canvas-downloader/releases)
- Decompress the archive file
- Run the executable directly or move it to `$PATH`

For macOS, the following command may be needed because the binary is not signed with an Apple developer account:

```bash
xattr -d com.apple.quarantine canvas-downloader
```

## Quick Start

### 1. Create Configuration File

You can copy the [example config](examples/config.toml) into one of the configuration file locations searched in order:

1. Custom path via `--config`
2. `canvas-downloader.toml` in the current directory
3. Platform-specific config directory:
   - Linux: `~/.config/canvas-downloader/config.toml`
   - macOS: `~/.config/canvas-downloader/config.toml` or `~/Library/Application Support/canvas-downloader/config.toml`
   - Windows: `%APPDATA%\canvas-downloader\config.toml`

Then set your Canvas instance URL and access token.

#### How to get your token

Log in to Canvas → Account → Settings → **New Access Token**.

### 2. Discover Your Courses

Run the tool without course filters:

```shell
canvas-downloader
```

It will list available terms, course codes, and course names.

### 3. Download Your Courses

Download by term:

```shell
canvas-downloader -t 115 120
```

Download specific courses:

```shell
canvas-downloader -c CS1101S "Introduction to Data Structures"
```

Combine term and course filters:

```shell
canvas-downloader -t 115 -c CS1101S
```

The tool shows the files that will be downloaded and their sizes before asking for confirmation.

Course name matching is exact. Use the course code or course name exactly as shown by the discovery output.

## What Gets Downloaded

- [x] Files
- [x] Modules
- [x] Syllabi in HTML and JSON
- [x] Files embedded in syllabus HTML
- [x] Assignments in HTML and JSON
- [x] Discussions and announcements in HTML and JSON
- [x] Pages in HTML and JSON
- [x] User information in JSON
- [ ] Panopto lecture videos (experimental)

## Syllabus attachments

Canvas syllabi can contain course files that are not otherwise exposed through the normal Files or Modules structure.

`canvas-downloader` resolves embedded Canvas file references and queues them like ordinary course files.

Supported syllabus references include:

- `<a>` links to `/courses/<course_id>/files/<file_id>`
- `<img>` preview URLs for Canvas course files

Canvas file references are resolved through:

```text
/api/v1/files/<file_id>
```

This ensures the downloader uses the canonical Canvas filename, metadata, and download URL instead of saving preview URLs as files named `preview`.

Repeated references to the same Canvas file in a single HTML body are de-duplicated before download.

Embedded syllabus files are stored under a `syllabus/` directory next to `syllabus.html`.

Example:

```text
Course/
├── syllabus.html
└── syllabus/
    ├── lecture-1.pdf
    ├── exercises.pdf
    └── banner.png
```

## Common Workflows

### Filter What You Download

Create a `.canvasignore` file in your current directory to skip files using `.gitignore` syntax:

```text
*.mp4
*.mov

/CS1101S/

/lecture-recordings/
```

You can also specify another ignore file:

```shell
canvas-downloader -t 115 -i custom-ignore.txt
```

See [examples/.canvasignore](examples/.canvasignore) for examples.

### Keep Your Files Updated

Use `-n` to overwrite local files when Canvas reports a newer version:

```shell
canvas-downloader -t 115 -n
```

By default, existing local files are not overwritten.

### Preview Changes

Use `--dry-run` to inspect what would be downloaded without writing the queued files:

```shell
canvas-downloader -t 115 --dry-run
```

### Choose Download Location

Specify a custom destination with `-d`:

```shell
canvas-downloader -t 115 -d ~/Canvas
```

### See Debug Information

Use `-v` for verbose logging:

```shell
canvas-downloader -t 115 -v
```

Without `-v`, only important progress messages are shown.

## All Options

```text
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
