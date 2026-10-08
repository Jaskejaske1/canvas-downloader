#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

COURSES = {
    "network-architecture": "Network Architecture (YT0745 _ 2026)",
    "php-mysql": "PHP & MySQL (YT0708 _ 2026)",
    "python": "Python (YT0895 _ 2026)",
    "virtualisation": "Virtualisation (YT0747 _ 2026)",
}

CANVAS_FILE_RE = re.compile(r"/courses/\d+/files/(\d+)")
CANVAS_PAGE_RE = re.compile(r"/courses/\d+/pages/([^/?#]+)")
WEEK_RE = re.compile(r"(?i)\bweek[\s_-]*(\d+)\b")
FAQ_RE = re.compile(r"(?i)\bfaq(?:'s)?\b|frequently\s+asked\s+questions")

LAB_HINTS = ("[oefen]", "oefen", "exercise", "exercises", "labo", "lab", "practice", "praktijk", "opgave")
SETUP_HINTS = ("[installeer]", "installeer", "install", "setup", "environment")
THEORY_HINTS = ("theorie", "theory", "les ", "lecture", "slides")

DECORATIVE_SYLLABUS_NAMES = {
    "alt_over_1.png",
    "banner_1.png",
    "banner_template_nl_2_1.jpg",
    "contact_1.png",
    "nl_studiemateriaal_1.png",
    "python_logo_only_500px.jpg",
    "virtualization_technology_tech_gee_technology_gee.webp",
    "kw2v030lt1619773191.jpg",
}


def ascii_slug(text: str, sep: str = "_") -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", sep, text)
    return re.sub(re.escape(sep) + r"{2,}", sep, text).strip(sep)


def compact_file_name(name: str) -> str:
    """Preserve already-compact lecturer filenames; normalize filenames with spaces."""
    p = Path(name)
    if not re.search(r"\s", p.name):
        return p.name
    return f"{ascii_slug(p.stem)}{p.suffix.lower()}"


def asset_file_name(name: str) -> str:
    p = Path(name)
    return f"{ascii_slug(p.stem)}{p.suffix.lower()}"


def md_name(stem: str) -> str:
    return f"{ascii_slug(stem)}.md"


@dataclass
class PlanRow:
    course: str
    source: str
    status: str
    destination: str
    rule: str


@dataclass
class HtmlRef:
    attr: str
    url: str
    label: str


@dataclass
class RefRow:
    course: str
    source: str
    attr: str
    label: str
    url: str
    action: str
    target: str
    note: str


class RefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[HtmlRef] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._anchor_hint = ""

    def handle_starttag(self, tag: str, attrs):
        attrs = {k.lower(): v for k, v in attrs if k and v is not None}
        tag = tag.lower()

        if tag == "a" and "href" in attrs:
            self._anchor_href = attrs["href"]
            self._anchor_text = []
            self._anchor_hint = (
                attrs.get("title")
                or attrs.get("download")
                or attrs.get("aria-label")
                or ""
            ).strip()

        if tag in {"img", "source", "video", "audio", "iframe"} and "src" in attrs:
            label = attrs.get("alt") or attrs.get("title") or ""
            self.refs.append(HtmlRef("src", attrs["src"], label.strip()))

            # If an image/media element is wrapped in an anchor, its alt/title
            # text is also useful for resolving the anchor target.
            if self._anchor_href is not None and label.strip():
                self._anchor_text.append(label.strip())

    def handle_data(self, data: str):
        if self._anchor_href is not None:
            text = re.sub(r"\s+", " ", html.unescape(data)).strip()
            if text:
                self._anchor_text.append(text)

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._anchor_href is not None:
            label = re.sub(r"\s+", " ", " ".join(self._anchor_text)).strip()
            if not label:
                label = self._anchor_hint
            self.refs.append(HtmlRef("href", self._anchor_href, label))
            self._anchor_href = None
            self._anchor_text = []
            self._anchor_hint = ""


def parse_refs(path: Path) -> list[HtmlRef]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = RefParser()
    parser.feed(raw)
    parser.close()

    seen = set()
    out = []
    for ref in parser.refs:
        key = (ref.attr, ref.url, ref.label)
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return out


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def contains_any(text: str, hints: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(h in low for h in hints)


def week_from_path(source: str) -> str | None:
    m = WEEK_RE.search(source)
    return f"{int(m.group(1)):02d}" if m else None


def nearest_semantic_kind(source: str) -> str | None:
    parts = PurePosixPath(source).parts[:-1]
    for part in reversed(parts):
        low = part.lower()
        if contains_any(low, SETUP_HINTS):
            return "setup"
        if contains_any(low, LAB_HINTS):
            return "lab"
        if contains_any(low, THEORY_HINTS):
            return "theory"
    return None


def plan_network(source: str, suffix: str) -> PlanRow:
    course = "network-architecture"
    p = PurePosixPath(source)

    if source == "syllabus.html":
        return PlanRow(course, source, "MAPPED", f"{course}/course/syllabus.md", "syllabus HTML -> Markdown")

    if source.startswith("announcements/") and suffix == ".html":
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/announcements/{md_name(p.stem)}",
                       "announcement HTML -> Markdown")

    if source.startswith("discussions/") and suffix == ".html":
        if FAQ_RE.search(p.stem):
            dest = f"{course}/course/faq.md"
        else:
            dest = f"{course}/course/discussions/{md_name(p.stem)}"
        return PlanRow(course, source, "MAPPED", dest, "discussion HTML -> Markdown")

    if source.startswith("syllabus/"):
        name = asset_file_name(p.name)
        if name in DECORATIVE_SYLLABUS_NAMES:
            return PlanRow(course, source, "SKIPPED", "", "known decorative syllabus asset")
        return PlanRow(course, source, "REVIEW", "", "unknown syllabus asset")

    if source == "modules/Theorie/Network Architecture - 00 - Afspraken.pdf":
        return PlanRow(course, source, "MAPPED", f"{course}/course/agreements.pdf", "agreements")

    m = re.match(r"^modules/Theorie/Network Architecture - (\d+)\s*-\s*(.+)\.pdf$", source)
    if m:
        n = f"{int(m.group(1)):02d}"
        return PlanRow(course, source, "MAPPED",
                       f"{course}/theory/{n}_{ascii_slug(m.group(2))}.pdf",
                       "numbered theory")

    m = re.match(r"^modules/Labo/Lab (\d+)\s*-\s*(.+)\.docx$", source)
    if m:
        n = f"{int(m.group(1)):02d}"
        return PlanRow(course, source, "MAPPED",
                       f"{course}/labs/{n}_{ascii_slug(m.group(2))}.docx",
                       "numbered lab")

    return PlanRow(course, source, "UNMAPPED", "", "no matching rule")


def plan_virtualisation(source: str, suffix: str) -> PlanRow:
    course = "virtualisation"
    p = PurePosixPath(source)

    if source == "syllabus.html":
        return PlanRow(course, source, "MAPPED", f"{course}/course/syllabus.md", "syllabus HTML -> Markdown")

    if source.startswith("discussions/") and suffix == ".html":
        if FAQ_RE.search(p.stem):
            dest = f"{course}/course/faq.md"
        else:
            dest = f"{course}/course/discussions/{md_name(p.stem)}"
        return PlanRow(course, source, "MAPPED", dest, "discussion HTML -> Markdown")

    if source.startswith("syllabus/"):
        name = asset_file_name(p.name)
        if name in DECORATIVE_SYLLABUS_NAMES:
            return PlanRow(course, source, "SKIPPED", "", "known decorative syllabus asset")
        return PlanRow(course, source, "REVIEW", "", "unknown syllabus asset")

    if source == "modules/Theorie/Virtualization - 0 - Afspraken.pdf":
        return PlanRow(course, source, "MAPPED", f"{course}/course/agreements.pdf", "agreements")

    m = re.match(r"^modules/Theorie/Virtualization - (\d+)\s*-\s*(.+)\.pdf$", source)
    if m:
        n = f"{int(m.group(1)):02d}"
        return PlanRow(course, source, "MAPPED",
                       f"{course}/theory/{n}_{ascii_slug(m.group(2))}.pdf",
                       "numbered theory")

    if source == "modules/Labo/OS Fundamentals - Lab environment.pdf":
        return PlanRow(course, source, "MAPPED", f"{course}/labs/environment.pdf", "lab environment")

    m = re.match(r"^modules/Labo/Virtualization lab (\d+)\s*-\s*(.+)\.docx$", source)
    if m:
        n = f"{int(m.group(1)):02d}"
        return PlanRow(course, source, "MAPPED",
                       f"{course}/labs/{n}_{ascii_slug(m.group(2))}.docx",
                       "numbered lab")

    return PlanRow(course, source, "UNMAPPED", "", "no matching rule")


def plan_python(source: str, suffix: str) -> PlanRow:
    course = "python"
    p = PurePosixPath(source)

    if source == "syllabus.html":
        return PlanRow(course, source, "MAPPED", f"{course}/course/syllabus.md", "syllabus HTML -> Markdown")

    if source.startswith("discussions/") and suffix == ".html":
        if FAQ_RE.search(p.stem):
            dest = f"{course}/course/faq.md"
        else:
            dest = f"{course}/course/discussions/{md_name(p.stem)}"
        return PlanRow(course, source, "MAPPED", dest, "discussion HTML -> Markdown")

    if source.startswith("assignments/") and suffix == ".html":
        return PlanRow(course, source, "MAPPED",
                       f"{course}/assignments/{md_name(p.stem)}",
                       "assignment HTML -> Markdown")

    if source.startswith("syllabus/"):
        name = p.name
        normalized_asset = asset_file_name(name)
        if normalized_asset in DECORATIVE_SYLLABUS_NAMES:
            return PlanRow(course, source, "SKIPPED", "", "known decorative syllabus asset")
        if re.match(r"^Python-PPT-Chapter-\d+-.+\.pdf$", name):
            return PlanRow(course, source, "MAPPED", f"{course}/theory/{compact_file_name(name)}", "Python chapter")
        if re.match(r"^ex\d+\.pdf$", name):
            return PlanRow(course, source, "MAPPED", f"{course}/exercises/{name}", "numbered exercise")
        if name == "Python-PPT-Lab-Project.pdf":
            return PlanRow(course, source, "MAPPED", f"{course}/lab/{name}", "lab project")
        if re.match(r"^Sample_Exam(?:_with_solutions)?\.pdf$", name):
            return PlanRow(course, source, "MAPPED", f"{course}/exam/{name}", "sample exam")
        return PlanRow(course, source, "REVIEW", "", "unknown Python syllabus asset")

    m = re.match(r"^modules/Syllabus for this OPO/([^/]+)/([^/]+)\.html$", source)
    if m:
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/{md_name(m.group(2))}",
                       "OPO syllabus page -> Markdown")

    m = re.match(r"^modules/AI in this OPO/AI-use as a student/([^/]+)/([^/]+)\.html$", source)
    if m:
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/ai/student/{md_name(m.group(2))}",
                       "student AI guidance -> Markdown")

    m = re.match(r"^modules/AI in this OPO/AI-use by your lecturer/([^/]+)/([^/]+)\.html$", source)
    if m:
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/ai/lecturer/{md_name(m.group(2))}",
                       "lecturer AI guidance -> Markdown")

    if source.startswith("modules/AI in this OPO/") and suffix == ".url":
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/ai/{compact_file_name(p.name)}",
                       "AI external resource")

    return PlanRow(course, source, "UNMAPPED", "", "no matching rule")


def plan_php(source: str, suffix: str) -> PlanRow:
    course = "php-mysql"
    p = PurePosixPath(source)

    if source == "syllabus.html":
        return PlanRow(course, source, "MAPPED", f"{course}/course/syllabus.md", "syllabus HTML -> Markdown")

    if source.startswith("discussions/") and suffix == ".html":
        if FAQ_RE.search(p.stem):
            dest = f"{course}/course/faq.md"
        else:
            dest = f"{course}/course/discussions/{md_name(p.stem)}"
        return PlanRow(course, source, "MAPPED", dest, "discussion HTML -> Markdown")

    if source.startswith("syllabus/"):
        name = asset_file_name(p.name)
        if name in DECORATIVE_SYLLABUS_NAMES:
            return PlanRow(course, source, "SKIPPED", "", "known decorative syllabus asset")
        return PlanRow(course, source, "REVIEW", "", "unknown syllabus asset")

    m = re.match(r"^assignments/oplaadzone\s+([^/]+)\.html$", source)
    if m:
        slug = ascii_slug(m.group(1))
        return PlanRow(course, source, "MAPPED",
                       f"{course}/assignments/{slug}.md",
                       "assignment HTML -> Markdown")

    m = re.match(r"^assignments/oplaadzone\s+([^/]+)/(.+)$", source)
    if m:
        assignment = ascii_slug(m.group(1))
        name = asset_file_name(Path(m.group(2)).name)
        return PlanRow(course, source, "MAPPED",
                       f"{course}/assignments/assets/{assignment}/{name}",
                       "assignment referenced asset")

    if source == "modules/examenopdracht labo/AssignmentPHP.pdf":
        return PlanRow(course, source, "MAPPED", f"{course}/exam/AssignmentPHP.pdf", "exam assignment")

    m = re.match(r"^modules/Studiewijzer voor PHP-MySQL/([^/]+)/([^/]+)\.html$", source)
    if m:
        topic = ascii_slug(m.group(1))
        return PlanRow(course, source, "MAPPED",
                       f"{course}/course/{topic}.md",
                       "study guide page -> Markdown")

    m = re.match(r"^modules/Studiewijzer voor PHP-MySQL/([^/]+)/([^/]+)/(.+)$", source)
    if m:
        topic = ascii_slug(m.group(1))
        name = Path(m.group(3)).name
        if is_image(name):
            dest = f"{course}/course/assets/{topic}/{asset_file_name(name)}"
            rule = "study guide embedded asset"
        else:
            dest = f"{course}/course/{topic}/{compact_file_name(name)}"
            rule = "study guide attachment"
        return PlanRow(course, source, "MAPPED", dest, rule)

    week = week_from_path(source)
    if source.startswith("modules/Week") and week:
        rest = "/".join(PurePosixPath(source).parts[1:])
        name = p.name

        if week == "03" and rest == "exercise_copy_start.php":
            return PlanRow(course, source, "OVERRIDE",
                           f"{course}/week_03/lab/exercise_copy_start.php",
                           "explicit Week 3 lab starter override")

        kind = nearest_semantic_kind(source)

        if suffix == ".html":
            out_name = md_name(p.stem)
        else:
            out_name = compact_file_name(name)

        if kind == "setup":
            return PlanRow(course, source, "MAPPED",
                           f"{course}/week_{week}/lab/setup/{out_name}",
                           "nearest semantic segment: setup")
        if kind == "lab":
            return PlanRow(course, source, "MAPPED",
                           f"{course}/week_{week}/lab/{out_name}",
                           "nearest semantic segment: lab")
        if kind == "theory":
            return PlanRow(course, source, "MAPPED",
                           f"{course}/week_{week}/theory/{out_name}",
                           "nearest semantic segment: theory")

        return PlanRow(course, source, "UNMAPPED", "", "week detected but semantic type unknown")

    return PlanRow(course, source, "UNMAPPED", "", "no matching rule")


def build_plan(mirror: Path):
    rows: list[PlanRow] = []
    source_abs: dict[tuple[str, str], Path] = {}
    course_roots: dict[str, Path] = {}

    for course, dirname in COURSES.items():
        root = mirror / dirname
        course_roots[course] = root
        if not root.is_dir():
            print(f"WARNING: missing course root: {root}")
            continue

        for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda x: str(x).lower()):
            source = rel(root, path)
            suffix = path.suffix.lower()
            if course == "network-architecture":
                row = plan_network(source, suffix)
            elif course == "php-mysql":
                row = plan_php(source, suffix)
            elif course == "python":
                row = plan_python(source, suffix)
            else:
                row = plan_virtualisation(source, suffix)

            rows.append(row)
            source_abs[(course, source)] = path

    return rows, source_abs, course_roots


def iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_dicts(value)


def build_raw_file_id_name_index(mirror: Path) -> dict[tuple[str, str], set[str]]:
    """Best-effort Canvas file-id -> filename index from raw JSON."""
    index: dict[tuple[str, str], set[str]] = defaultdict(set)

    for course, dirname in COURSES.items():
        raw_root = mirror / "raw" / dirname
        if not raw_root.is_dir():
            continue

        for path in raw_root.rglob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue

            for obj in iter_dicts(data):
                file_id = obj.get("id")
                if not isinstance(file_id, (int, str)):
                    continue

                # Avoid indexing arbitrary Canvas objects unless they look file-like.
                url_values = " ".join(str(obj.get(k, "")) for k in ("url", "html_url", "preview_url"))
                looks_file = (
                    "/files/" in url_values
                    or any(k in obj for k in ("filename", "display_name", "content-type", "content_type"))
                )
                if not looks_file:
                    continue

                for key in ("filename", "display_name", "name"):
                    value = obj.get(key)
                    if isinstance(value, str) and Path(value).suffix:
                        index[(course, str(file_id))].add(Path(value).name)

    return index


def build_basename_index(rows: list[PlanRow]) -> dict[tuple[str, str], list[PlanRow]]:
    index: dict[tuple[str, str], list[PlanRow]] = defaultdict(list)
    for row in rows:
        if row.status in {"MAPPED", "OVERRIDE", "SKIPPED", "REVIEW"}:
            index[(row.course, PurePosixPath(row.source).name.lower())].append(row)
    return index


def build_html_slug_index(rows: list[PlanRow]) -> dict[tuple[str, str], list[PlanRow]]:
    index: dict[tuple[str, str], list[PlanRow]] = defaultdict(list)
    for row in rows:
        if row.source.lower().endswith(".html") and row.destination:
            stem = PurePosixPath(row.source).stem
            index[(row.course, ascii_slug(stem, "-"))].append(row)
            index[(row.course, ascii_slug(stem, "_"))].append(row)
    return index


def page_asset_dir(root: Path, source: str) -> Path:
    src = root / Path(source.replace("/", "\\"))
    if source == "syllabus.html":
        return root / "syllabus"
    return src.with_suffix("")


def candidate_rows_in_asset_dir(
    course: str,
    source: str,
    course_root: Path,
    plan_by_source: dict[tuple[str, str], PlanRow],
) -> list[PlanRow]:
    folder = page_asset_dir(course_root, source)
    if not folder.is_dir():
        return []

    out = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        candidate_source = rel(course_root, path)
        row = plan_by_source.get((course, candidate_source))
        if row:
            out.append(row)
    return out


def relative_target(from_dest: str, to_dest: str) -> str:
    from_dir = PurePosixPath(from_dest).parent
    target = PurePosixPath(to_dest)

    # PurePosixPath has no relpath helper.
    import posixpath
    return posixpath.relpath(str(target), str(from_dir))


def _row_resolution(row: PlanRow, dest: str, reason: str):
    if row.status == "SKIPPED":
        return "DROP_DECORATIVE", "", f"{reason}: skipped {row.source}", row.source
    if row.destination:
        return "REWRITE_LOCAL", relative_target(dest, row.destination), f"{reason}: {row.source}", row.source
    return "UNRESOLVED_REFERENCE", "", f"{reason}: candidate has no destination", ""


def _label_candidates(label: str, asset_candidates: list[PlanRow]) -> list[PlanRow]:
    """Resolve a human-visible link label against nearby local filenames."""
    if not label:
        return []

    label_name = PurePosixPath(label.replace("\\\\", "/")).name.strip()
    if not label_name:
        return []

    label_path = PurePosixPath(label_name)
    label_stem = ascii_slug(label_path.stem)
    label_suffix = label_path.suffix.lower()

    # 1. Exact basename, case-insensitive.
    exact = [
        row for row in asset_candidates
        if PurePosixPath(row.source).name.casefold() == label_name.casefold()
    ]
    if len(exact) == 1:
        return exact

    # 2. Exact normalized stem (+ compatible extension if supplied).
    normalized = []
    for row in asset_candidates:
        candidate = PurePosixPath(row.source)
        if label_suffix and candidate.suffix.lower() != label_suffix:
            continue
        if ascii_slug(candidate.stem) == label_stem:
            normalized.append(row)
    if len(normalized) == 1:
        return normalized

    # 3. Canvas often introduces numeric duplicate suffixes such as Banner-1.
    def strip_numeric_suffix(stem: str) -> str:
        return re.sub(r"(?:_\\d+)+$", "", stem)

    relaxed = []
    for row in asset_candidates:
        candidate = PurePosixPath(row.source)
        if label_suffix and candidate.suffix.lower() != label_suffix:
            continue
        candidate_stem = strip_numeric_suffix(ascii_slug(candidate.stem))
        if candidate_stem == strip_numeric_suffix(label_stem):
            relaxed.append(row)
    if len(relaxed) == 1:
        return relaxed

    # 4. Conservative fuzzy fallback. Prefer the longest matching stem so
    # "Sample_Exam_with_solutions" wins over the shorter "Sample_Exam".
    scored: list[tuple[int, PlanRow]] = []
    for row in asset_candidates:
        candidate = PurePosixPath(row.source)
        if label_suffix and candidate.suffix.lower() != label_suffix:
            continue
        stem = ascii_slug(candidate.stem)
        if label_stem and (label_stem in stem or stem in label_stem):
            scored.append((len(stem), row))

    if scored:
        best_len = max(score for score, _ in scored)
        best = [row for score, row in scored if score == best_len]
        if len(best) == 1:
            return best

    return []


def resolve_file_ref(
    course: str,
    source: str,
    dest: str,
    file_id: str,
    label: str,
    raw_id_index,
    basename_index,
    asset_candidates: list[PlanRow],
):
    # 1. Raw metadata gives us the canonical filename when available.
    names = raw_id_index.get((course, file_id), set())
    matched: list[PlanRow] = []
    for name in names:
        matched.extend(basename_index.get((course, name.lower()), []))

    unique = {(r.source, r.destination, r.status): r for r in matched}
    matched = list(unique.values())
    if len(matched) == 1:
        return _row_resolution(matched[0], dest, f"Canvas file {file_id}")

    # 2. Match visible anchor text, title/download/aria-label, or image alt text
    # against assets stored next to the HTML source.
    label_matches = _label_candidates(label, asset_candidates)
    if len(label_matches) == 1:
        return _row_resolution(label_matches[0], dest, f"label '{label}'")

    # 3. If the page has exactly one nearby candidate total, it is deterministic.
    if len(asset_candidates) == 1:
        return _row_resolution(asset_candidates[0], dest, "single nearby asset")

    # If every nearby asset is explicitly decorative, this embedded reference
    # can safely be dropped even when Canvas metadata is incomplete.
    if asset_candidates and all(r.status == "SKIPPED" for r in asset_candidates):
        return "DROP_DECORATIVE", "", "all nearby assets are explicitly decorative", ""

    return "UNRESOLVED_REFERENCE", "", f"could not resolve Canvas file id {file_id}", ""


def build_reference_plan(
    rows: list[PlanRow],
    source_abs: dict[tuple[str, str], Path],
    course_roots: dict[str, Path],
    mirror: Path,
) -> list[RefRow]:
    ref_rows: list[RefRow] = []
    plan_by_source = {(r.course, r.source): r for r in rows}
    basename_index = build_basename_index(rows)
    page_slug_index = build_html_slug_index(rows)
    raw_id_index = build_raw_file_id_name_index(mirror)

    for row in rows:
        if not row.source.lower().endswith(".html"):
            continue
        if row.status not in {"MAPPED", "OVERRIDE"} or not row.destination:
            continue

        path = source_abs[(row.course, row.source)]
        refs = parse_refs(path)
        asset_candidates = candidate_rows_in_asset_dir(
            row.course, row.source, course_roots[row.course], plan_by_source
        )

        page_rows: list[RefRow] = []
        used_candidate_sources: set[str] = set()
        unresolved_canvas: list[tuple[int, HtmlRef, str]] = []

        for ref in refs:
            url = ref.url.strip()
            parsed = urlparse(url)
            path_part = unquote(parsed.path or "")

            file_match = CANVAS_FILE_RE.search(path_part)
            if file_match:
                action, target, note, matched_source = resolve_file_ref(
                    row.course,
                    row.source,
                    row.destination,
                    file_match.group(1),
                    ref.label,
                    raw_id_index,
                    basename_index,
                    asset_candidates,
                )
                page_rows.append(
                    RefRow(row.course, row.source, ref.attr, ref.label, url, action, target, note)
                )
                if matched_source:
                    used_candidate_sources.add(matched_source)
                if action == "UNRESOLVED_REFERENCE":
                    unresolved_canvas.append((len(page_rows) - 1, ref, file_match.group(1)))
                continue

            page_match = CANVAS_PAGE_RE.search(path_part)
            if page_match:
                page_slug = page_match.group(1).strip("/")
                candidates = page_slug_index.get((row.course, ascii_slug(page_slug, "-")), [])
                if len(candidates) == 1 and candidates[0].destination:
                    target = relative_target(row.destination, candidates[0].destination)
                    action, note = "REWRITE_LOCAL", f"Canvas page -> {candidates[0].source}"
                else:
                    target = ""
                    action, note = "UNRESOLVED_REFERENCE", f"Canvas page slug '{page_slug}' not uniquely resolved"
                page_rows.append(
                    RefRow(row.course, row.source, ref.attr, ref.label, url, action, target, note)
                )
                continue

            if parsed.scheme in {"http", "https", "mailto", "tel"}:
                page_rows.append(
                    RefRow(row.course, row.source, ref.attr, ref.label, url, "KEEP_EXTERNAL", url, "external reference")
                )
                continue

            if url.startswith("#"):
                page_rows.append(
                    RefRow(row.course, row.source, ref.attr, ref.label, url, "KEEP_FRAGMENT", url, "in-page fragment")
                )
                continue

            # Relative source reference.
            if not parsed.scheme and path_part:
                src_parent = PurePosixPath(row.source).parent
                candidate = str((src_parent / path_part).as_posix())
                import posixpath
                candidate = posixpath.normpath(candidate)
                target_row = plan_by_source.get((row.course, candidate))
                if target_row and target_row.destination:
                    target = relative_target(row.destination, target_row.destination)
                    page_rows.append(
                        RefRow(row.course, row.source, ref.attr, ref.label, url, "REWRITE_LOCAL", target, f"relative source -> {candidate}")
                    )
                else:
                    page_rows.append(
                        RefRow(row.course, row.source, ref.attr, ref.label, url, "UNRESOLVED_REFERENCE", "", f"relative source not mapped: {candidate}")
                    )
                continue

            page_rows.append(
                RefRow(row.course, row.source, ref.attr, ref.label, url, "UNRESOLVED_REFERENCE", "", "unhandled reference form")
            )

        # Final offline fallback: after all filename/metadata matches have been
        # consumed, resolve by elimination only when the assignment is unique.
        #
        # We intentionally do NOT assign N unresolved refs to N remaining files:
        # ordering is not a trustworthy contract. We only accept a single
        # unresolved Canvas ref with a single compatible unused nearby asset.
        if len(unresolved_canvas) == 1:
            idx, ref, file_id = unresolved_canvas[0]

            remaining = [
                candidate
                for candidate in asset_candidates
                if candidate.source not in used_candidate_sources
            ]

            if ref.attr == "src":
                compatible = [
                    candidate for candidate in remaining
                    if is_image(PurePosixPath(candidate.source).name)
                ]
            else:
                non_images = [
                    candidate for candidate in remaining
                    if not is_image(PurePosixPath(candidate.source).name)
                ]
                compatible = non_images if non_images else remaining

            if len(compatible) == 1:
                candidate = compatible[0]
                action, target, note, matched_source = _row_resolution(
                    candidate,
                    row.destination,
                    f"unique remaining asset for Canvas file {file_id}",
                )
                old = page_rows[idx]
                page_rows[idx] = RefRow(
                    old.course,
                    old.source,
                    old.attr,
                    old.label,
                    old.url,
                    action,
                    target,
                    note,
                )
                if matched_source:
                    used_candidate_sources.add(matched_source)

        ref_rows.extend(page_rows)

    return ref_rows


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            d = asdict(row)
            writer.writerow({k: d[k] for k in fields})


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only TM INFRA publish-plan audit.")
    parser.add_argument(
        "mirror",
        nargs="?",
        default=r"C:\Users\Jaske\source\repos\EA-ICT\canvas-mirror",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    mirror = Path(args.mirror)
    if not mirror.is_dir():
        raise SystemExit(f"Mirror not found: {mirror}")

    if args.out:
        out_dir = Path(args.out)
    else:
        out_dir = Path(tempfile.gettempdir()) / f"canvas-publish-plan-{datetime.now():%Y%m%d-%H%M%S}"

    out_dir.mkdir(parents=True, exist_ok=False)

    rows, source_abs, course_roots = build_plan(mirror)
    refs = build_reference_plan(rows, source_abs, course_roots, mirror)

    collisions = defaultdict(list)
    for row in rows:
        if row.destination and row.status in {"MAPPED", "OVERRIDE"}:
            collisions[row.destination.lower()].append(row)
    collisions = {k: v for k, v in collisions.items() if len(v) > 1}

    write_csv(
        out_dir / "publish-plan.csv",
        rows,
        ["course", "source", "status", "destination", "rule"],
    )
    write_csv(
        out_dir / "reference-plan.csv",
        refs,
        ["course", "source", "attr", "label", "url", "action", "target", "note"],
    )

    print()
    print("=== PUBLISH PLAN SUMMARY ===")
    for course in COURSES:
        course_rows = [r for r in rows if r.course == course]
        counts = Counter(r.status for r in course_rows)
        summary = "  ".join(f"{k}={counts.get(k,0)}" for k in ("MAPPED", "OVERRIDE", "SKIPPED", "REVIEW", "UNMAPPED"))
        print(f"{course:22} {summary}")

    print()
    unmapped = [r for r in rows if r.status == "UNMAPPED"]
    review = [r for r in rows if r.status == "REVIEW"]
    unresolved = [r for r in refs if r.action == "UNRESOLVED_REFERENCE"]

    print(f"UNMAPPED:              {len(unmapped)}")
    print(f"REVIEW FILES:          {len(review)}")
    print(f"DESTINATION COLLISIONS:{len(collisions)}")
    print(f"UNRESOLVED REFERENCES: {len(unresolved)}")

    if unmapped:
        print()
        print("=== UNMAPPED ===")
        for r in unmapped:
            print(f"{r.course}: {r.source} :: {r.rule}")

    if review:
        print()
        print("=== REVIEW FILES ===")
        for r in review:
            print(f"{r.course}: {r.source} :: {r.rule}")

    if collisions:
        print()
        print("=== DESTINATION COLLISIONS ===")
        for _, group in sorted(collisions.items()):
            print(group[0].destination)
            for r in group:
                print(f"  {r.course}: {r.source}")

    if unresolved:
        print()
        print("=== UNRESOLVED REFERENCES ===")
        for r in unresolved:
            label = f' label="{r.label}"' if r.label else ""
            print(f"{r.course}/{r.source} [{r.attr}]{label}")
            print(f"  {r.url}")
            print(f"  {r.note}")

    print()
    print("=== REFERENCE ACTIONS ===")
    counts = Counter(r.action for r in refs)
    for key in sorted(counts):
        print(f"{key:22} {counts[key]}")

    print()
    print(f"Output: {out_dir}")
    print("  publish-plan.csv")
    print("  reference-plan.csv")

    if not unmapped and not review and not collisions and not unresolved:
        print()
        print("PLAN IS CLEAN: ready for publisher implementation.")
    else:
        print()
        print("PLAN NEEDS REVIEW: no files were written to OneDrive.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
