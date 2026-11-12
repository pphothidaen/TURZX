#!/usr/bin/env python3
"""Verification script for markdown relative links and documentation integrity.

Validates that all relative markdown links in USER_GUIDE.md, README.md, and docs/*
resolve to existing files, and internal anchors match document headers.
"""

from pathlib import Path
import sys
import unicodedata
import re


def extract_markdown_links(text: str):
    """Extract (is_image, label, url) from markdown text, handling nested brackets."""
    links = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == '[':
            bracket_depth = 1
            j = i + 1
            while j < n and bracket_depth > 0:
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '[':
                    bracket_depth += 1
                elif text[j] == ']':
                    bracket_depth -= 1
                j += 1
            if bracket_depth == 0 and j < n and text[j] == '(':
                label = text[i + 1:j - 1]
                paren_depth = 1
                k = j + 1
                while k < n and paren_depth > 0:
                    if text[k] == '\\':
                        k += 2
                        continue
                    if text[k] == '(':
                        paren_depth += 1
                    elif text[k] == ')':
                        paren_depth -= 1
                    k += 1
                if paren_depth == 0:
                    url = text[j + 1:k - 1].strip()
                    is_image = (i > 0 and text[i - 1] == '!')
                    links.append((is_image, label, url))
                    if not is_image:
                        inner = extract_markdown_links(label)
                        links.extend(inner)
                    i = k
                    continue
        i += 1
    return links


def gfm_slug(text: str) -> str:
    """Generate GitHub Flavored Markdown heading anchor slug."""
    slug = text.strip().lower()
    result = []
    for c in slug:
        cat = unicodedata.category(c)
        if cat.startswith(('P', 'S')):
            continue
        result.append(c)
    slug = ''.join(result)
    slug = re.sub(r'\s+', '-', slug)
    return slug


def check_docs() -> int:
    base_dir = Path(__file__).parent.resolve()
    doc_files = [base_dir / "USER_GUIDE.md", base_dir / "README.md"] + sorted((base_dir / "docs").glob("*.md"))

    total_links = 0
    broken_links = []
    checked_files = 0

    print("=" * 65)
    print("TURZX Documentation Relative Link Checker")
    print("=" * 65)

    for doc_path in doc_files:
        if not doc_path.exists():
            print(f"❌ File missing: {doc_path}")
            broken_links.append((str(doc_path), "File missing", "", "Target document not found"))
            continue

        checked_files += 1
        content = doc_path.read_text(encoding="utf-8")
        links = extract_markdown_links(content)

        # Collect local heading slugs for anchor validation
        headers = re.findall(r'^#{1,6}\s+(.+)$', content, re.MULTILINE)
        local_anchors = {gfm_slug(h) for h in headers}

        print(f"\nChecking {doc_path.relative_to(base_dir)} ({len(links)} total links found):")

        for is_img, label, url in links:
            # Skip external protocols
            if url.startswith(("http://", "https://", "mailto:")) or not url:
                continue

            total_links += 1

            # Anchor within same file
            if url.startswith("#"):
                target_anchor = url[1:]
                if target_anchor in local_anchors:
                    print(f"  ✓ [ANCHOR OK] #{target_anchor}")
                else:
                    print(f"  ❌ [BROKEN ANCHOR] {url}")
                    broken_links.append((str(doc_path.relative_to(base_dir)), label, url, "Anchor not found"))
                continue

            # Relative path
            url_parts = url.split("#", 1)
            file_part = url_parts[0]
            anchor_part = url_parts[1] if len(url_parts) > 1 else None

            target_file = (doc_path.parent / file_part).resolve()

            if not target_file.exists():
                print(f"  ❌ [BROKEN LINK] {label} -> {url} (resolved to {target_file})")
                broken_links.append(
                    (str(doc_path.relative_to(base_dir)), label, url, f"File {target_file} does not exist")
                )
            else:
                if anchor_part:
                    target_content = target_file.read_text(encoding="utf-8")
                    target_headers = re.findall(r'^#{1,6}\s+(.+)$', target_content, re.MULTILINE)
                    target_anchors = {gfm_slug(h) for h in target_headers}
                    if anchor_part in target_anchors:
                        print(f"  ✓ [FILE+ANCHOR OK] {url} -> {target_file.relative_to(base_dir)}#{anchor_part}")
                    else:
                        print(f"  ❌ [BROKEN ANCHOR IN TARGET] {url}")
                        broken_links.append(
                            (
                                str(doc_path.relative_to(base_dir)),
                                label,
                                url,
                                f"Anchor #{anchor_part} not found in {target_file}",
                            )
                        )
                else:
                    print(f"  ✓ [FILE OK] {url} -> {target_file.relative_to(base_dir)}")

    print("\n" + "=" * 65)
    print(f"Summary: Checked {checked_files} documents, {total_links} relative links.")
    if broken_links:
        print(f"❌ Found {len(broken_links)} broken link(s):")
        for doc, lbl, u, reason in broken_links:
            print(f"  - In {doc}: [{lbl}]({u}) -> {reason}")
        return 1
    else:
        print("✅ All relative markdown links and anchors resolved successfully!")
        return 0


if __name__ == "__main__":
    sys.exit(check_docs())
