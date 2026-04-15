"""Generate ADR index for the cosalette MCP server.

Parses all ADR files in docs/adr/ and creates a compact JSON index with
metadata and full content for MCP tools to use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_yaml_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Returns:
        Tuple of (frontmatter_dict, content_without_frontmatter)
    """
    frontmatter = {}
    content_lines = content.split("\n")

    if content_lines and content_lines[0].strip() == "---":
        # Find the end of frontmatter
        end_index = 1
        while (
            end_index < len(content_lines) and content_lines[end_index].strip() != "---"
        ):
            line = content_lines[end_index].strip()
            if line and ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                # Handle lists ([tag1, tag2])
                if value.startswith("[") and value.endswith("]"):
                    # Simple list parsing - remove brackets and split by comma
                    items = value[1:-1].split(",")
                    frontmatter[key] = [
                        item.strip().strip('"').strip("'")
                        for item in items
                        if item.strip()
                    ]
                else:
                    # Remove quotes
                    frontmatter[key] = value.strip("\"'")
            end_index += 1

        # Skip the frontmatter and the closing --- line
        if end_index < len(content_lines):
            content_without_frontmatter = "\n".join(content_lines[end_index + 1 :])
        else:
            content_without_frontmatter = content
    else:
        content_without_frontmatter = content

    return frontmatter, content_without_frontmatter


def extract_title_from_content(content: str) -> str:
    """Extract title from ADR content."""
    # Look for ADR-NNN: Title pattern in headings
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            # Remove heading markers
            title_line = line.lstrip("#").strip()
            if title_line.startswith("ADR-"):
                # Extract title after the ADR-NNN: part
                if ":" in title_line:
                    return title_line.split(":", 1)[1].strip()
                else:
                    return title_line

    return "Unknown Title"


def extract_summary_from_content(content: str) -> str:
    """Extract summary (first paragraph of Context section) from ADR content."""
    lines = content.split("\n")
    context_started = False
    summary_lines = []

    for line in lines:
        line_stripped = line.strip()

        # Look for Context section
        if line_stripped.startswith("##") and "context" in line_stripped.lower():
            context_started = True
            continue

        # Stop if we hit another section
        if context_started and line_stripped.startswith("##"):
            break

        # Collect content under Context section
        if context_started:
            if line_stripped and not line_stripped.startswith("#"):
                summary_lines.append(line_stripped)
            elif summary_lines:  # Stop at first empty line after content
                break

    # Return first non-empty paragraph
    summary = " ".join(summary_lines).strip()
    if summary:
        # Truncate to first sentence or reasonable length
        sentences = summary.split(".")
        if sentences and len(sentences[0]) < 200:
            return sentences[0] + "."
        elif len(summary) > 200:
            return summary[:200] + "..."
        else:
            return summary

    return "No summary available."


def parse_adr_file(file_path: Path) -> dict[str, Any]:
    """Parse a single ADR file and extract metadata and content."""
    content = file_path.read_text(encoding="utf-8")
    frontmatter, content_body = parse_yaml_frontmatter(content)

    # Extract ID from filename (ADR-001-title.md -> ADR-001)
    adr_id = file_path.stem.split("-")[0:2]  # ['ADR', '001']
    adr_id = f"{adr_id[0]}-{adr_id[1]}"

    title = extract_title_from_content(content_body)
    summary = extract_summary_from_content(content_body)

    return {
        "id": adr_id,
        "title": title,
        "status": frontmatter.get("status", "Unknown"),
        "date": frontmatter.get("date", "Unknown"),
        "impact": frontmatter.get("impact", "Unknown"),
        "tags": frontmatter.get("tags", []),
        "summary": summary,
        "content": content,  # Include full markdown content
    }


def generate_adr_index() -> None:
    """Generate ADR index JSON file."""
    # Find workspace root
    script_path = Path(__file__)
    workspace_root = script_path.parent.parent

    docs_dir = workspace_root / "docs" / "adr"
    output_dir = (
        workspace_root / "packages" / "src" / "cosalette" / "assets" / "guidance"
    )
    output_file = output_dir / "adr-index.json"

    if not docs_dir.exists():
        print(f"❌ ADR directory not found: {docs_dir}")
        return

    # Find all ADR files
    adr_files = list(docs_dir.glob("ADR-*.md"))
    if not adr_files:
        print(f"❌ No ADR files found in {docs_dir}")
        return

    print(f"📋 Found {len(adr_files)} ADR files")

    # Parse each file
    adrs = []
    for adr_file in sorted(adr_files):
        try:
            adr_data = parse_adr_file(adr_file)
            adrs.append(adr_data)
            print(f"✅ Parsed {adr_data['id']}: {adr_data['title']}")
        except Exception as e:
            print(f"❌ Failed to parse {adr_file}: {e}")

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write index
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(adrs, f, indent=2, ensure_ascii=False)

    print(f"✅ Generated ADR index: {output_file}")
    print(f"📊 Indexed {len(adrs)} ADRs")


if __name__ == "__main__":
    generate_adr_index()
