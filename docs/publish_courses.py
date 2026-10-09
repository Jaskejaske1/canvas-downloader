# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "beautifulsoup4>=4.12",
#   "markdownify>=0.13",
# ]
# ///
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as to_markdown


DEFAULT_MIRROR = Path(
    r"C:\Users\Jaske\source\repos\EA-ICT\canvas-mirror"
)
DEFAULT_TARGET = Path(
    r"C:\Users\Jaske\OneDrive - bvba Demunter\School\Thomas More"
    r"\FASE_2_SSS\Vakken"
)


def load_planner(script_dir: Path):
    planner_path = script_dir / "plan_publish.py"
    if not planner_path.is_file():
        raise SystemExit(f"Planner not found next to publisher: {planner_path}")

    spec = importlib.util.spec_from_file_location("tm_infra_plan_publish", planner_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load planner: {planner_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def validate_plan(planner, rows, refs):
    problems: list[str] = []

    unmapped = [r for r in rows if r.status == "UNMAPPED"]
    review = [r for r in rows if r.status == "REVIEW"]
    unresolved = [r for r in refs if r.action == "UNRESOLVED_REFERENCE"]

    collisions = defaultdict(list)
    for row in rows:
        if row.status in {"MAPPED", "OVERRIDE"} and row.destination:
            collisions[row.destination.casefold()].append(row)
    collisions = {k: v for k, v in collisions.items() if len(v) > 1}

    if unmapped:
        problems.append(f"{len(unmapped)} UNMAPPED file(s)")
    if review:
        problems.append(f"{len(review)} REVIEW file(s)")
    if collisions:
        problems.append(f"{len(collisions)} destination collision(s)")
    if unresolved:
        problems.append(f"{len(unresolved)} unresolved reference(s)")

    if problems:
        print("PUBLISH ABORTED: plan is not clean.")
        for problem in problems:
            print(f"  - {problem}")
        print()
        print("Run plan_publish.py first and resolve the reported items.")
        raise SystemExit(2)

    return {
        "unmapped": 0,
        "review": 0,
        "collisions": 0,
        "unresolved": 0,
    }


def build_ref_index(refs):
    """Index the clean reference plan for deterministic HTML rewriting."""
    index = {}

    for ref in refs:
        key = (ref.course, ref.source, ref.attr, ref.url)
        value = (ref.action, ref.target, ref.note)

        old = index.get(key)
        if old is not None and old != value:
            raise SystemExit(
                "Conflicting reference actions for "
                f"{ref.course}/{ref.source} [{ref.attr}] {ref.url}"
            )
        index[key] = value

    return index


def rewrite_html(
    raw_html: str,
    *,
    course: str,
    source: str,
    ref_index,
) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # Rewrite anchors.
    for tag in list(soup.find_all("a")):
        url = tag.get("href")
        if not url:
            continue

        plan = ref_index.get((course, source, "href", url))
        if plan is None:
            # The audit must have seen every href. Failing closed prevents
            # silently leaking stale Canvas-internal links into the library.
            raise RuntimeError(
                f"Missing href plan: {course}/{source} -> {url}"
            )

        action, target, _note = plan

        if action == "REWRITE_LOCAL":
            tag["href"] = target
        elif action in {"KEEP_EXTERNAL", "KEEP_FRAGMENT"}:
            pass
        elif action == "DROP_DECORATIVE":
            tag.unwrap()
        else:
            raise RuntimeError(
                f"Unsupported href action {action}: {course}/{source} -> {url}"
            )

    # Rewrite embedded media.
    for tag in list(soup.find_all(["img", "source", "video", "audio", "iframe"])):
        url = tag.get("src")
        if not url:
            continue

        plan = ref_index.get((course, source, "src", url))
        if plan is None:
            raise RuntimeError(
                f"Missing src plan: {course}/{source} -> {url}"
            )

        action, target, _note = plan

        if action == "REWRITE_LOCAL":
            tag["src"] = target
        elif action == "KEEP_EXTERNAL":
            pass
        elif action == "DROP_DECORATIVE":
            tag.decompose()
        else:
            raise RuntimeError(
                f"Unsupported src action {action}: {course}/{source} -> {url}"
            )

    # The downloaded files are content fragments more often than complete sites.
    # Converting the body avoids carrying Canvas document chrome if present.
    fragment = soup.body if soup.body is not None else soup

    markdown = to_markdown(
        str(fragment),
        heading_style="ATX",
        bullets="-",
        # Canvas content sometimes contains Markdown-like punctuation as
        # literal text (for example **Theory:** or "1. arrays"). Escaping
        # those characters makes the published Markdown less useful.
        escape_asterisks=False,
        escape_underscores=False,
        escape_misc=False,
    )

    # Keep rewritten external destinations valid if the source/converter
    # escaped a URL scheme delimiter.
    markdown = re.sub(
        r"\((https?|mailto|tel)\\:",
        r"(\1:",
        markdown,
        flags=re.IGNORECASE,
    )

    # When an inline image is immediately followed by prose, give it a real
    # Markdown paragraph break.
    markdown = re.sub(
        r"(\!\[[^\n]*?\]\([^\n]*?\))(?=\S)",
        r"\1\n\n",
        markdown,
    )

    # Conservative whitespace cleanup only; preserve the lecturer's content.
    lines = [line.rstrip() for line in markdown.splitlines()]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            cleaned.append(line)
            blank = False
        elif not blank:
            cleaned.append("")
            blank = True

    result = "\n".join(cleaned).strip()
    return result + "\n" if result else ""


def render_tree(
    *,
    planner,
    rows,
    refs,
    source_abs,
    source_mirror: Path,
    output_root: Path,
):
    ref_index = build_ref_index(refs)

    copied = 0
    converted = 0
    bytes_copied = 0
    destinations: list[str] = []

    for row in sorted(rows, key=lambda r: (r.destination.casefold(), r.source.casefold())):
        if row.status not in {"MAPPED", "OVERRIDE"}:
            continue
        if not row.destination:
            raise RuntimeError(f"Mapped row has no destination: {row.course}/{row.source}")

        source_path = source_abs[(row.course, row.source)]
        destination_path = output_root / Path(row.destination.replace("/", os.sep))
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if source_path.suffix.lower() == ".html":
            raw = source_path.read_text(encoding="utf-8", errors="replace")
            markdown = rewrite_html(
                raw,
                course=row.course,
                source=row.source,
                ref_index=ref_index,
            )
            destination_path.write_text(markdown, encoding="utf-8", newline="\n")
            converted += 1
        else:
            shutil.copy2(source_path, destination_path)
            copied += 1
            try:
                bytes_copied += source_path.stat().st_size
            except OSError:
                pass

        destinations.append(row.destination)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_mirror": str(source_mirror),
        "managed_courses": list(planner.COURSES.keys()),
        "files_total": len(destinations),
        "html_converted_to_markdown": converted,
        "files_copied": copied,
        "bytes_copied": bytes_copied,
        "validation": {
            "unmapped": 0,
            "review_files": 0,
            "destination_collisions": 0,
            "unresolved_references": 0,
        },
    }

    (output_root / "_publish_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "copied": copied,
        "converted": converted,
        "bytes_copied": bytes_copied,
        "destinations": destinations,
    }


def verify_render(rows, output_root: Path):
    expected = {
        row.destination
        for row in rows
        if row.status in {"MAPPED", "OVERRIDE"} and row.destination
    }

    missing = []
    for destination in sorted(expected):
        path = output_root / Path(destination.replace("/", os.sep))
        if not path.is_file():
            missing.append(destination)

    if missing:
        print("Rendered tree is incomplete:")
        for item in missing:
            print(f"  MISSING {item}")
        raise SystemExit(3)


def verify_markdown(output_root: Path):
    """
    Fail closed on the specific Markdown escaping problems observed during
    preview validation. This runs against the rendered files, not console
    output, so chat/terminal escaping cannot produce false positives.
    """
    suspicious_patterns = {
        r"\*\*": "escaped bold marker",
        r"\(": "escaped opening parenthesis",
        r"\)": "escaped closing parenthesis",
        r"\:": "escaped URL colon",
        r"\-": "escaped list marker",
    }

    issues: list[tuple[Path, str, str]] = []

    for path in sorted(output_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")

        for needle, description in suspicious_patterns.items():
            if needle in text:
                issues.append((path, needle, description))

    if issues:
        print()
        print("=== MARKDOWN VALIDATION FAILED ===")
        for path, needle, description in issues:
            relative = path.relative_to(output_root)
            print(f"{relative}: {description} ({needle})")
        print()
        print("Nothing will be published.")
        raise SystemExit(4)

    print("Markdown validation:     OK")


def replace_managed_courses(
    *,
    planner,
    rendered_root: Path,
    target_root: Path,
):
    target_root.mkdir(parents=True, exist_ok=True)

    operation_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    preserved_local_files = 0

    try:
        for course in planner.COURSES:
            source_course = rendered_root / course
            if not source_course.is_dir():
                raise RuntimeError(f"Rendered course missing: {source_course}")

            target_course = target_root / course
            backup_course = target_root / f".{course}.backup-{operation_id}"

            # Preserve the user-maintained local overlay across Canvas refreshes.
            # `local/` is explicitly outside the managed Canvas output and may
            # contain notes, Packet Tracer files, study context, or legacy
            # reference material. The planner must never generate `local/`.
            local_source = target_course / "local"
            local_destination = source_course / "local"

            if local_source.is_dir():
                if local_destination.exists():
                    raise RuntimeError(
                        f"Managed output collides with preserved local overlay: "
                        f"{local_destination}"
                    )
                shutil.copytree(local_source, local_destination, copy_function=shutil.copy2)
                preserved_local_files += sum(
                    1 for path in local_destination.rglob("*") if path.is_file()
                )

            if backup_course.exists():
                raise RuntimeError(f"Backup path already exists: {backup_course}")

            if target_course.exists():
                target_course.rename(backup_course)
                backups.append((target_course, backup_course))

            shutil.move(str(source_course), str(target_course))
            installed.append(target_course)

        # Publish manifest last, after all managed course folders are installed.
        manifest_src = rendered_root / "_publish_manifest.json"
        if manifest_src.is_file():
            shutil.copy2(manifest_src, target_root / "_publish_manifest.json")

    except Exception:
        # Roll back this publish attempt as far as possible.
        for target_course in reversed(installed):
            if target_course.exists():
                shutil.rmtree(target_course)

        for target_course, backup_course in reversed(backups):
            if backup_course.exists() and not target_course.exists():
                backup_course.rename(target_course)

        raise

    else:
        # Cleanup is best-effort only. OneDrive can temporarily hold handles on
        # old folders after a rename/move. A cleanup failure must not turn an
        # otherwise successful publish into a failed publish.
        cleanup_failures = []
        for _target_course, backup_course in backups:
            if not backup_course.exists():
                continue
            try:
                shutil.rmtree(backup_course)
            except OSError as exc:
                cleanup_failures.append((backup_course, exc))

        if cleanup_failures:
            print()
            print("WARNING: publish succeeded, but some old backup folders")
            print("could not be removed because Windows/OneDrive still has them locked:")
            for backup_course, exc in cleanup_failures:
                print(f"  {backup_course}")
                print(f"    {exc}")
            print("These folders are old backups only and can be removed later.")

    return preserved_local_files


def print_summary(
    rows,
    refs,
    render_info,
    preview_root,
    *,
    apply,
    target,
    preserved_local_files=0,
):
    status_counts = Counter(row.status for row in rows)
    ref_counts = Counter(ref.action for ref in refs)

    print()
    print("=== PUBLISH SUMMARY ===")
    print(f"Mapped files:           {status_counts.get('MAPPED', 0)}")
    print(f"Overrides:              {status_counts.get('OVERRIDE', 0)}")
    print(f"Skipped source assets:  {status_counts.get('SKIPPED', 0)}")
    print(f"HTML -> Markdown:       {render_info['converted']}")
    print(f"Files copied as-is:     {render_info['copied']}")
    if apply:
        print(f"Local overlay preserved:{preserved_local_files:3}")
    print()
    print("Reference actions:")
    for action in sorted(ref_counts):
        print(f"  {action:20} {ref_counts[action]}")

    print()
    if apply:
        print(f"Published to: {target}")
    else:
        print("DRY RUN / PREVIEW ONLY")
        print(f"Rendered preview: {preview_root}")
        print("Nothing was written to OneDrive.")
        print()
        print("Inspect the preview, then rerun with --apply.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TM INFRA Canvas mirror -> curated course library publisher."
    )
    parser.add_argument(
        "--mirror",
        type=Path,
        default=DEFAULT_MIRROR,
        help=f"Canvas mirror [default: {DEFAULT_MIRROR}]",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"OneDrive course library [default: {DEFAULT_TARGET}]",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually replace the four managed course folders in the target.",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    planner = load_planner(script_dir)

    rows, source_abs, course_roots = planner.build_plan(args.mirror)
    refs = planner.build_reference_plan(
        rows,
        source_abs,
        course_roots,
        args.mirror,
    )

    validate_plan(planner, rows, refs)

    preview_root = Path(tempfile.gettempdir()) / (
        f"canvas-course-publish-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    )
    preview_root.mkdir(parents=True, exist_ok=False)

    try:
        render_info = render_tree(
            planner=planner,
            rows=rows,
            refs=refs,
            source_abs=source_abs,
            source_mirror=args.mirror,
            output_root=preview_root,
        )
        verify_render(rows, preview_root)
        verify_markdown(preview_root)

        preserved_local_files = 0
        if args.apply:
            preserved_local_files = replace_managed_courses(
                planner=planner,
                rendered_root=preview_root,
                target_root=args.target,
            )

        print_summary(
            rows,
            refs,
            render_info,
            preview_root,
            apply=args.apply,
            target=args.target,
            preserved_local_files=preserved_local_files,
        )

    except Exception:
        print()
        print(f"Preview/build directory retained for inspection: {preview_root}")
        raise

    if args.apply:
        # Anything left is only the manifest / empty staging structure.
        shutil.rmtree(preview_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
