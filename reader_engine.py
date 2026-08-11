"""
reader_engine.py

Handles:
- Extracting readable text from .epub and .pdf files
- Splitting text into sentences
- Grouping sentences into "chunks" (like dialogue/text boxes)

Kept dependency-light on purpose (ebooklib, beautifulsoup4, pdfminer.six)
since heavier libs (e.g. PyMuPDF/lxml with C extensions) are more likely
to cause headaches when compiled for Android via Buildozer.
"""

import os
import re
import json
import hashlib

from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub
from pdfminer.high_level import extract_text as pdf_extract_text

# A conservative sentence splitter. Handles ., !, ? as terminators while
# trying not to split on common abbreviations (Mr., Mrs., Dr., e.g., etc.)
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "e.g", "i.e", "fig", "no", "vol", "cf", "approx",
}

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')


def split_sentences(paragraph_text):
    """Split a block of text into a list of sentences."""
    text = re.sub(r'\s+', ' ', paragraph_text).strip()
    if not text:
        return []

    raw_pieces = _SENTENCE_SPLIT_RE.split(text)

    sentences = []
    buffer = ""
    for piece in raw_pieces:
        if buffer:
            buffer = buffer + " " + piece
        else:
            buffer = piece

        # Check if buffer ends in a likely-abbreviation before treating
        # this as a genuine sentence boundary.
        last_word = re.findall(r'([A-Za-z\.]+)\.$', buffer)
        is_abbrev = False
        if last_word:
            candidate = last_word[0].lower().rstrip('.')
            if candidate in _ABBREVIATIONS:
                is_abbrev = True

        if is_abbrev:
            continue  # keep accumulating into buffer
        else:
            sentences.append(buffer.strip())
            buffer = ""

    if buffer.strip():
        sentences.append(buffer.strip())

    return [s for s in sentences if s]


def _extract_epub_paragraphs(filepath):
    """Return list of paragraph strings, in reading order, with chapter markers."""
    book = epub.read_epub(filepath)
    paragraphs = []

    title = "Untitled"
    try:
        meta = book.get_metadata('DC', 'title')
        if meta:
            title = meta[0][0]
    except Exception:
        pass

    for item in book.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), 'html.parser')

            # Chapter heading, if present, becomes its own "paragraph"
            # marked so the UI can render it distinctly if desired.
            for heading in soup.find_all(['h1', 'h2', 'h3']):
                heading_text = heading.get_text(strip=True)
                if heading_text:
                    paragraphs.append({"type": "heading", "text": heading_text})
                heading.extract()

            for p in soup.find_all(['p', 'div', 'li']):
                text = p.get_text(separator=' ', strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    paragraphs.append({"type": "para", "text": text})

    return title, paragraphs


def _extract_pdf_paragraphs(filepath):
    """PDFs have no reliable chapter structure; treat as one flow of paragraphs."""
    raw_text = pdf_extract_text(filepath) or ""
    title = os.path.splitext(os.path.basename(filepath))[0]

    # pdfminer separates paragraphs loosely with blank lines; fall back to
    # single-newline splitting if double-newlines aren't present.
    blocks = re.split(r'\n\s*\n', raw_text)
    if len(blocks) <= 1:
        blocks = raw_text.split('\n')

    paragraphs = []
    for block in blocks:
        text = re.sub(r'\s+', ' ', block).strip()
        if text:
            paragraphs.append({"type": "para", "text": text})

    return title, paragraphs


def extract_book(filepath):
    """
    Extract (title, paragraphs) from an epub or pdf file.
    paragraphs: list of {"type": "para"|"heading", "text": str}
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.epub':
        return _extract_epub_paragraphs(filepath)
    elif ext == '.pdf':
        return _extract_pdf_paragraphs(filepath)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def build_chunks(paragraphs, sentences_per_chunk=3):
    """
    Turn paragraphs into a flat list of chunk dicts:
        {"type": "heading", "text": "..."}
        {"type": "text", "sentences": ["...", "...", "..."]}

    Headings always start a new chunk on their own.
    A chunk never spans across a heading, but it *can* span across a
    paragraph break if that's needed to fill sentences_per_chunk -
    this keeps the box count-based rather than paragraph-based, matching
    the "video game textbox" feel.
    """
    chunks = []
    current_sentences = []

    def flush():
        nonlocal current_sentences
        if current_sentences:
            chunks.append({"type": "text", "sentences": current_sentences})
            current_sentences = []

    for para in paragraphs:
        if para["type"] == "heading":
            flush()
            chunks.append({"type": "heading", "text": para["text"]})
            continue

        sentences = split_sentences(para["text"])
        for sentence in sentences:
            current_sentences.append(sentence)
            if len(current_sentences) >= sentences_per_chunk:
                flush()

    flush()
    return chunks


def cache_key_for_file(filepath, sentences_per_chunk):
    """Stable cache key based on file contents + chunk size, so re-parsing
    is skipped unless the file or chunk setting changes."""
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        # Hash only first+last 64KB + size for speed on large files.
        size = os.path.getsize(filepath)
        hasher.update(str(size).encode())
        chunk = f.read(65536)
        hasher.update(chunk)
        if size > 65536:
            f.seek(max(0, size - 65536))
            hasher.update(f.read(65536))
    hasher.update(str(sentences_per_chunk).encode())
    return hasher.hexdigest()[:16]


def load_or_build_chunks(filepath, cache_dir, sentences_per_chunk=3):
    """
    Returns (title, chunks), using a JSON cache in cache_dir keyed by
    file content hash + chunk size so we don't re-parse every open.
    """
    os.makedirs(cache_dir, exist_ok=True)
    key = cache_key_for_file(filepath, sentences_per_chunk)
    cache_path = os.path.join(cache_dir, f"{key}.json")

    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data["title"], data["chunks"]

    title, paragraphs = extract_book(filepath)
    chunks = build_chunks(paragraphs, sentences_per_chunk=sentences_per_chunk)

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({"title": title, "chunks": chunks}, f)

    return title, chunks
