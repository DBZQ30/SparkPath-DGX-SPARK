"""Tests for structure-aware document chunker."""

from knowledge_base.ingestion.semantic_splitter import chunk_document, _split_into_sections


def test_markdown_headings():
    text = """# Title
Intro text.
## Section 1
Content of section 1.
## Section 2
Content of section 2.
"""
    chunks = chunk_document(text, "test.md", "global")
    assert len(chunks) == 3
    assert chunks[0]["section_title"] == "Title"
    assert chunks[1]["section_title"] == "Section 1"
    assert chunks[2]["section_title"] == "Section 2"


def test_chinese_numbered_sections():
    text = """前言
这是前言内容。

一、培养目标
培养高级人才。

二、学分要求
总学分45分。
"""
    chunks = chunk_document(text, "doc.txt", "global")
    assert len(chunks) == 3
    assert "前言" in chunks[0]["content"]
    assert chunks[1]["section_title"] == "一、培养目标"
    assert chunks[2]["section_title"] == "二、学分要求"


def test_no_headings():
    """Plain text without headings becomes a single chunk."""
    text = """Line 1
Line 2
Line 3"""
    chunks = chunk_document(text, "plain.txt", "global")
    assert len(chunks) == 1
    assert "Line 1" in chunks[0]["content"]


def test_mixed_heading_and_plain():
    """Text with some headings but also preamble."""
    text = """This is preamble.
It has multiple lines.

## Topic 1
Here is the topic.
"""
    chunks = chunk_document(text, "mix.txt", "global")
    assert len(chunks) == 2
    assert chunks[0]["section_title"] is None
    assert chunks[1]["section_title"] == "Topic 1"


def test_oversized_section_fallback():
    """Sections exceeding max_chunk_lines are split with line-based fallback."""
    text = "# Big Section\n" + "\n".join(f"Line {i}" for i in range(100))
    chunks = chunk_document(text, "big.txt", "global", max_chunk_lines=30)
    assert len(chunks) > 1
    assert all(c["section_title"].startswith("Big Section") for c in chunks)


def test_empty_content():
    chunks = chunk_document("", "empty.txt", "global")
    assert chunks == []


def test_section_extraction():
    sections = _split_into_sections("A\nB\n## C\nD\nE")
    assert len(sections) == 2
    # _split_into_sections returns (heading, section_path, content) triples
    assert sections[0] == ("", "", "A\nB")
    assert sections[1] == ("C", "C", "D\nE")


def test_english_numbered_sections():
    text = """Chapter 1 Introduction
Content here.
Chapter 2 Methods
More content.
"""
    chunks = chunk_document(text, "paper.txt", "global")
    assert len(chunks) >= 2
    assert chunks[0]["section_title"] == "Chapter 1 Introduction"
    assert chunks[1]["section_title"] == "Chapter 2 Methods"


def test_parser_version_tag():
    chunks = chunk_document("## A\ntext", "f.txt", "global", parser_version="semantic-v1")
    assert chunks[0]["parser_version"] == "semantic-v1"


def test_source_hash_propagation():
    chunks = chunk_document("## A\ntext", "f.txt", "global", source_hash="abc")
    assert chunks[0]["source_hash"] == "abc"
