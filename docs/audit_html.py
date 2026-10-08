#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse, unquote

COURSES = {
    "network-architecture": "Network Architecture (YT0745 _ 2026)",
    "php-mysql": "PHP & MySQL (YT0708 _ 2026)",
    "python": "Python (YT0895 _ 2026)",
    "virtualisation": "Virtualisation (YT0747 _ 2026)",
}

CANVAS_FILE_RE = re.compile(r"/courses/\d+/files/(\d+)")
WHITESPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)


class AuditHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        attr_map = {k.lower(): v for k, v in attrs if k and v is not None}

        if tag in {"script", "style", "noscript", "svg"}:
            self._hidden_depth += 1

        if tag == "a" and "href" in attr_map:
            self.links.append(("href", attr_map["href"]))
        elif tag in {"img", "source", "video", "audio", "iframe"}:
            if "src" in attr_map:
                self.links.append(("src", attr_map["src"]))

        if "srcset" in attr_map:
            for candidate in attr_map["srcset"].split(","):
                url = candidate.strip().split(" ", 1)[0]
                if url:
                    self.links.append(("srcset", url))

    def handle_endtag(self, tag: str):
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str):
        if self._hidden_depth == 0:
            text = WHITESPACE_RE.sub(" ", html.unescape(data)).strip()
            if text:
                self.text_parts.append(text)


@dataclass
class RefInfo:
    attr: str
    url: str
    kind: str
    canvas_file_id: str
    basename: str


def normalize_text(parts: list[str]) -> str:
    return WHITESPACE_RE.sub(" ", " ".join(parts)).strip()


def classify_ref(attr: str, url: str) -> RefInfo:
    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    match = CANVAS_FILE_RE.search(path)
    canvas_file_id = match.group(1) if match else ""

    suffix = Path(path).suffix.lower()
    basename = Path(path.rstrip("/")).name if path else ""

    if attr in {"src", "srcset"}:
        kind = "embedded"
    elif canvas_file_id:
        kind = "canvas_file_link"
    elif url.startswith("#"):
        kind = "fragment"
    elif parsed.scheme in {"http", "https"}:
        kind = "external_link"
    elif suffix:
        kind = "relative_file"
    else:
        kind = "relative_link"

    return RefInfo(
        attr=attr,
        url=url,
        kind=kind,
        canvas_file_id=canvas_file_id,
        basename=basename,
    )


def read_html(path: Path) -> tuple[str, list[RefInfo]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    parser = AuditHTMLParser()
    parser.feed(raw)
    parser.close()

    text = normalize_text(parser.text_parts)

    seen: set[tuple[str, str]] = set()
    refs: list[RefInfo] = []
    for attr, url in parser.links:
        key = (attr, url)
        if key in seen:
            continue
        seen.add(key)
        refs.append(classify_ref(attr, url))

    return text, refs


def likely_content(text_chars: int, word_count: int, refs: list[RefInfo]) -> str:
    embedded = sum(r.kind == "embedded" for r in refs)
    links = sum(r.attr == "href" for r in refs)

    if word_count >= 40 or text_chars >= 250:
        return "YES"
    if word_count >= 10:
        return "REVIEW"
    if embedded or links:
        return "REVIEW"
    return "LOW"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit of HTML pages in the TM INFRA Canvas mirror."
    )
    parser.add_argument(
        "mirror",
        nargs="?",
        default=r"C:\Users\Jaske\source\repos\EA-ICT\canvas-mirror",
        help="Path to canvas-mirror",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output directory. Defaults to a timestamped folder under TEMP.",
    )
    args = parser.parse_args()

    mirror = Path(args.mirror)
    if not mirror.is_dir():
        raise SystemExit(f"Mirror not found: {mirror}")

    if args.out:
        out_dir = Path(args.out)
    else:
        import tempfile
        from datetime import datetime

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(tempfile.gettempdir()) / f"canvas-html-audit-{stamp}"

    out_dir.mkdir(parents=True, exist_ok=False)

    page_rows: list[dict[str, object]] = []
    ref_rows: list[dict[str, object]] = []

    for course, dirname in COURSES.items():
        root = mirror / dirname
        if not root.is_dir():
            print(f"WARNING: missing course root: {root}")
            continue

        for path in sorted(root.rglob("*.html"), key=lambda p: str(p).lower()):
            rel = path.relative_to(root).as_posix()
            text, refs = read_html(path)
            words = WORD_RE.findall(text)

            hrefs = [r for r in refs if r.attr == "href"]
            embedded = [r for r in refs if r.attr in {"src", "srcset"}]
            canvas_file_refs = [r for r in refs if r.canvas_file_id]

            excerpt = text[:500]
            page_rows.append(
                {
                    "Course": course,
                    "Source": rel,
                    "TextChars": len(text),
                    "Words": len(words),
                    "HrefCount": len(hrefs),
                    "EmbeddedCount": len(embedded),
                    "CanvasFileRefs": len(canvas_file_refs),
                    "LikelyContent": likely_content(len(text), len(words), refs),
                    "Excerpt": excerpt,
                }
            )

            for ref in refs:
                ref_rows.append(
                    {
                        "Course": course,
                        "Source": rel,
                        "Attr": ref.attr,
                        "Kind": ref.kind,
                        "CanvasFileId": ref.canvas_file_id,
                        "Basename": ref.basename,
                        "URL": ref.url,
                    }
                )

    pages_csv = out_dir / "html-pages.csv"
    refs_csv = out_dir / "html-references.csv"

    with pages_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Course",
            "Source",
            "TextChars",
            "Words",
            "HrefCount",
            "EmbeddedCount",
            "CanvasFileRefs",
            "LikelyContent",
            "Excerpt",
        ])
        writer.writeheader()
        writer.writerows(page_rows)

    with refs_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Course",
            "Source",
            "Attr",
            "Kind",
            "CanvasFileId",
            "Basename",
            "URL",
        ])
        writer.writeheader()
        writer.writerows(ref_rows)

    print()
    print("=== HTML PAGE SUMMARY ===")
    print(f"Pages:      {len(page_rows)}")
    print(f"References: {len(ref_rows)}")

    by_course = Counter(row["Course"] for row in page_rows)
    by_content = Counter(row["LikelyContent"] for row in page_rows)

    print()
    for course in COURSES:
        print(f"{course:22} {by_course.get(course, 0):3} HTML pages")

    print()
    print("Likely content:")
    for status in ("YES", "REVIEW", "LOW"):
        print(f"  {status:6} {by_content.get(status, 0)}")

    print()
    print("=== PAGES ===")
    for row in sorted(page_rows, key=lambda r: (r["Course"], r["Source"])):
        print(
            f'{row["LikelyContent"]:6} '
            f'{row["Words"]:4} words  '
            f'{row["EmbeddedCount"]:2} embeds  '
            f'{row["HrefCount"]:2} links  '
            f'{row["Course"]}/{row["Source"]}'
        )

    print()
    print("=== EMBEDDED / CANVAS FILE REFERENCES ===")
    interesting_refs = [
        row for row in ref_rows
        if row["Attr"] in {"src", "srcset"} or row["CanvasFileId"]
    ]
    if interesting_refs:
        for row in interesting_refs:
            file_id = f' file={row["CanvasFileId"]}' if row["CanvasFileId"] else ""
            print(
                f'{row["Course"]}/{row["Source"]} '
                f'[{row["Attr"]}/{row["Kind"]}{file_id}] -> {row["URL"]}'
            )
    else:
        print("none")

    print()
    print(f"Output: {out_dir}")
    print(f"  {pages_csv.name}")
    print(f"  {refs_csv.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
