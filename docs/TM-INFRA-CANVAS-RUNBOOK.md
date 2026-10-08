# TM INFRA — Canvas mirror runbook

This document describes the current Canvas acquisition flow used for TM INFRA.

## Scope

This runbook covers only:

```text
Canvas
  ↓
canvas-downloader
  ↓
canvas-mirror
```

The mirror is the acquisition layer. Downstream tooling may read it, but only `canvas-downloader` should update it.

OneDrive publishing and course-specific semantic mapping are separate later stages.

## Local layout

```text
C:\Users\Jaske\source\repos\EA-ICT\
├── canvas-downloader\
│   ├── bin\
│   │   └── canvas-downloader.exe
│   ├── src\
│   └── target\
└── canvas-mirror\
    ├── Network Architecture (YT0745 _ 2026)\
    ├── PHP & MySQL (YT0708 _ 2026)\
    ├── Python (YT0895 _ 2026)\
    ├── Virtualisation (YT0747 _ 2026)\
    └── raw\
```

The operational executable is kept under `canvas-downloader\bin\`, not inside the mirror.

`bin/` is ignored by Git.

## Current source repository

Upstream:

```text
https://github.com/aik2mlj/canvas-downloader
```

Fork:

```text
https://github.com/Jaskejaske1/canvas-downloader
```

The fork was reset to current upstream before adding the syllabus attachment feature.

Relevant commits:

```text
a95ea76 feat: download files embedded in syllabi
5182697 chore: ignore local binary output
```

## Build requirements on Windows

Required local tooling:

```text
Rust / Cargo
CMake
NASM
```

Validated versions during setup:

```text
rustc 1.99.0
cargo 1.99.0
cmake 4.4.4
NASM 3.02
```

Build and test:

```powershell
cargo fmt
cargo test
cargo build --release
```

Copy the release binary into the operational location:

```powershell
New-Item -ItemType Directory -Force ".\bin" | Out-Null
Copy-Item ".\target\release\canvas-downloader.exe" ".\bin\canvas-downloader.exe" -Force
```

## Term and courses

Current Canvas term:

```text
294
```

Courses included in the regular Canvas mirror flow:

```text
Network Architecture (YT0745 | 2026)
PHP & MySQL (YT0708 | 2026)
Python (YT0895 | 2026)
Virtualisation (YT0747 | 2026)
```

Hacking Explained is not part of this regular Canvas flow.

## Mirror update command

```powershell
$Mirror = "C:\Users\Jaske\source\repos\EA-ICT\canvas-mirror"
$Downloader = "C:\Users\Jaske\source\repos\EA-ICT\canvas-downloader\bin\canvas-downloader.exe"

& $Downloader `
    --config "$Mirror\canvas-downloader.toml" `
    -d "$Mirror" `
    -t 294 `
    -c `
        "Network Architecture (YT0745 | 2026)" `
        "PHP & MySQL (YT0708 | 2026)" `
        "Python (YT0895 | 2026)" `
        "Virtualisation (YT0747 | 2026)" `
    --no-submissions `
    -n
```

`-n` allows existing mirror files to be replaced when Canvas reports a newer version.

Before confirming the download, verify that only the four expected courses are listed.

## Syllabus attachment behavior

The downloader now processes Canvas files embedded in syllabus HTML.

It detects both:

```text
<a href="/courses/<course>/files/<id>">
```

and Canvas image preview URLs such as:

```text
<img src="/courses/<course>/files/<id>/preview?...">
```

For Canvas file objects, the downloader resolves the file ID through:

```text
/api/v1/files/<id>
```

This preserves the canonical Canvas filename instead of saving preview URLs as files named `preview`.

Repeated references to the same Canvas file are de-duplicated before they enter the download queue.

## Python validation

The Python syllabus currently exposes 11 Canvas assets:

```text
Banner-1.PNG
python-logo_only_500px.jpg
Python-PPT-Lab-Project.pdf
Sample_Exam.pdf
Sample_Exam_with_solutions.pdf
Python-PPT-Chapter-1-Python-Basics.pdf
Python-PPT-Chapter-2-FlowControl.pdf
Python-PPT-Chapter-3-Functions.pdf
ex1.pdf
ex2.pdf
ex3.pdf
```

They are downloaded to:

```text
canvas-mirror\Python (YT0895 _ 2026)\syllabus\
```

The acquisition test confirmed:

```text
11 unique files
no duplicate Python-PPT-Lab-Project.pdf
no files named preview
successful real download
```

## Mirror semantics

The mirror is intentionally provider-shaped.

Examples:

```text
modules\
assignments\
discussions\
syllabus\
raw\
```

Do not manually reorganize these files inside the mirror.

Course-specific cleanup, semantic classification, filename normalization, and OneDrive publication happen downstream.

## Next stage

After acquisition is validated, continue with:

```text
canvas-mirror
  ↓
course-specific mapping
  ↓
OneDrive
```

The current mapping work should be based only on files that are confirmed to exist in the mirror.
