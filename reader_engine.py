"""
reader_engine.py

Turns an .epub or .pdf into a flat list of reading "units" (headings and
sentences), and groups those units into chunks of N sentences.

Dependencies are kept to the bare minimum on purpose, because every
third-party package has to survive python-for-android's packaging step:

- EPUB: Python standard library only (zipfile + ElementTree + html.parser).
- PDF:  pypdf, imported lazily inside the PDF function, so that a missing
        or broken pypdf can only ever affect opening a PDF - it can never
        stop the app from starting.
"""

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

# Bump when the parsing logic changes, so old cached results are ignored.
CACHE_VERSION = 2

# A "sentence" with no punctuation (tables, poetry, bad PDF text) is cut
# into pieces of at most this many characters so a chunk always fits.
MAX_SENTENCE_CHARS = 350

# ---------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------

_ABBREVIATIONS = {
    # English
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "e.g", "i.e",
    "fig", "vol", "cf", "approx", "capt", "col", "gen", "lt", "sgt", "rev",
    # Dutch
    "dhr", "mevr", "mw", "drs", "ir", "ing", "bijv", "bv", "o.a", "d.w.z",
    "m.b.t", "t.o.v", "i.p.v", "evt", "ca", "blz", "nr", "jl",
}

# Sentence-ending punctuation, optional closing quotes/brackets, then space.
_BOUNDARY_RE = re.compile(r'([.!?…]+)["\'”’)\]]*\s+')
_LAST_WORD_RE = re.compile(r'([^\W\d_]+(?:\.[^\W\d_]+)*)$')
_OPENERS = '"\'“‘(['


def _is_real_boundary(text, start, match):
    """Decide whether the punctuation at `match` really ends a sentence."""
    following = text[match.end():match.end() + 1]
    if not following:
        return False
    if not (following.isupper() or following.isdigit() or following in _OPENERS):
        return False
    if match.group(1) == ".":
        word = _LAST_WORD_RE.search(text[start:match.start()])
        if word:
            token = word.group(1)
            if token.lower() in _ABBREVIATIONS:
                return False
            if len(token) == 1 and token.isupper():  # initials: "J. K. Rowling"
                return False
    return True


def _split_long(sentence):
    """Cut an over-long sentence at a natural pause (; , or a space)."""
    pieces = []
    rest = sentence
    while len(rest) > MAX_SENTENCE_CHARS:
        window = rest[:MAX_SENTENCE_CHARS]
        cut = max(window.rfind("; "), window.rfind(", "))
        if cut < MAX_SENTENCE_CHARS // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = MAX_SENTENCE_CHARS - 1
        pieces.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def split_sentences(paragraph_text):
    """Split a block of text into a list of sentences."""
    text = re.sub(r"\s+", " ", paragraph_text or "").strip()
    if not text:
        return []

    sentences = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        if _is_real_boundary(text, start, match):
            sentences.append(text[start:match.end()].strip())
            start = match.end()
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)

    result = []
    for sentence in sentences:
        if len(sentence) > MAX_SENTENCE_CHARS:
            result.extend(_split_long(sentence))
        elif sentence:
            result.append(sentence)
    return result


# ---------------------------------------------------------------------
# EPUB (standard library only)
# ---------------------------------------------------------------------

_BLOCK_TAGS = {"p", "div", "li", "blockquote", "td", "dd", "dt"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4"}
_TRACKED_TAGS = _BLOCK_TAGS | _HEADING_TAGS
_SKIPPED_TAGS = {"script", "style", "head", "title"}


class _ParagraphExtractor(HTMLParser):
    """Walks XHTML content and collects paragraph/heading text blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs = []
        self._stack = []   # [tag_name, [text_parts]]
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag == "br":
            self.handle_data(" ")
        elif tag in _TRACKED_TAGS:
            # Text collected so far in an enclosing block is its own
            # paragraph, so reading order is preserved.
            if self._stack:
                self._flush(self._stack[-1])
                self._stack[-1][1] = []
            self._stack.append([tag, []])

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.handle_data(" ")
        elif tag in _TRACKED_TAGS:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag not in _TRACKED_TAGS:
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                closed = self._stack[i:]
                del self._stack[i:]
                for frame in closed:
                    self._flush(frame)
                return

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._stack:
            self._stack[-1][1].append(data)
        elif data.strip():
            # Loose text outside any block element: treat as a paragraph.
            self._stack.append(["p", [data]])

    def _flush(self, frame):
        tag, parts = frame
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        if not text:
            return
        kind = "heading" if tag in _HEADING_TAGS else "para"
        self.paragraphs.append({"type": kind, "text": text})

    def close(self):
        while self._stack:
            self._flush(self._stack.pop())
        super().close()


def _local_tag(tag):
    """Strip an XML namespace: '{...}rootfile' -> 'rootfile'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _decode(raw):
    for encoding in ("utf-8", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_epub_paragraphs(filepath):
    """Return (title or None, paragraphs) for an EPUB."""
    with zipfile.ZipFile(filepath, "r") as zf:
        names = {name.lower(): name for name in zf.namelist()}

        def read(path):
            real = names.get(path.lower())
            return zf.read(real) if real else None

        container = read("META-INF/container.xml")
        if container is None:
            raise ValueError("This file is not a valid EPUB (no container.xml).")

        opf_path = None
        for elem in ET.fromstring(container).iter():
            if _local_tag(elem.tag) == "rootfile":
                opf_path = elem.get("full-path")
                break
        opf_raw = read(opf_path) if opf_path else None
        if opf_raw is None:
            raise ValueError("This EPUB has no readable package (OPF) file.")

        opf_dir = os.path.dirname(opf_path)
        opf_root = ET.fromstring(opf_raw)

        title = None
        manifest = {}
        spine_ids = []
        for elem in opf_root.iter():
            tag = _local_tag(elem.tag)
            if tag == "title" and title is None and elem.text and elem.text.strip():
                title = elem.text.strip()
            elif tag == "item" and elem.get("id") and elem.get("href"):
                manifest[elem.get("id")] = (elem.get("href"), elem.get("media-type", ""))
            elif tag == "itemref" and elem.get("idref"):
                spine_ids.append(elem.get("idref"))

        if not spine_ids:
            spine_ids = [i for i, (_, mt) in manifest.items() if "html" in mt]

        paragraphs = []
        seen = set()
        for idref in spine_ids:
            href, media_type = manifest.get(idref, (None, ""))
            if not href or ("html" not in media_type and "xml" not in media_type):
                continue
            href = href.split("#", 1)[0]
            path = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
            if path in seen:
                continue
            seen.add(path)
            raw = read(path)
            if raw is None:
                continue
            extractor = _ParagraphExtractor()
            extractor.feed(_decode(raw))
            extractor.close()
            paragraphs.extend(extractor.paragraphs)

    return title, paragraphs


# ---------------------------------------------------------------------
# PDF (pypdf, imported lazily)
# ---------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r'[.!?…:]["\'”’)\]]*$')


def _extract_pdf_paragraphs(filepath):
    """Return (title or None, paragraphs) for a PDF."""
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - only on a broken build
        raise RuntimeError(
            "PDF support is not available in this build (pypdf is missing). "
            "EPUB files still work."
        ) from exc

    reader = PdfReader(filepath)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("This PDF is password-protected and cannot be opened.")

    title = None
    try:
        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip() or None
    except Exception:
        title = None

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    raw_text = "\n".join(pages)
    if not raw_text.strip():
        raise ValueError(
            "No text found in this PDF. It is probably a scan (images of "
            "pages), which ChunkReader cannot read."
        )

    # Re-join words hyphenated across line breaks.
    raw_text = re.sub(r"(\w)-\n(\w)", r"\1\2", raw_text)

    paragraphs = []
    pending = ""
    for block in re.split(r"\n\s*\n", raw_text):
        text = re.sub(r"\s+", " ", block).strip()
        if not text:
            continue
        pending = (pending + " " + text).strip() if pending else text
        # A block that stops mid-sentence (page break) joins the next one.
        if _SENTENCE_END_RE.search(pending) or len(pending) > 2000:
            paragraphs.append({"type": "para", "text": pending})
            pending = ""
    if pending:
        paragraphs.append({"type": "para", "text": pending})

    return title, paragraphs


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def sniff_book_type(filepath):
    """Return '.pdf' or '.epub' based on the file's first bytes, else None."""
    try:
        with open(filepath, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"PK"):
        return ".epub"
    return None


def extract_book(filepath):
    """Return (title or None, paragraphs) for an .epub or .pdf file."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in (".epub", ".pdf"):
        ext = sniff_book_type(filepath)
    if ext == ".epub":
        return _extract_epub_paragraphs(filepath)
    if ext == ".pdf":
        return _extract_pdf_paragraphs(filepath)
    raise ValueError("Unsupported file type. Only EPUB and PDF files can be opened.")


def paragraphs_to_units(paragraphs):
    """
    Flatten paragraphs into reading units:
        ["h", "Chapter title"]   a heading
        ["s", "One sentence."]   a sentence
    A reading position is simply an index into this list, which makes it
    independent of how many sentences are shown per chunk.
    """
    units = []
    for para in paragraphs:
        if para["type"] == "heading":
            units.append(["h", para["text"]])
        else:
            for sentence in split_sentences(para["text"]):
                units.append(["s", sentence])
    return units


def build_chunks(units, sentences_per_chunk=3):
    """
    Group units into chunks:
        {"type": "heading", "text": "...", "start": unit_index}
        {"type": "text", "sentences": [...], "start": unit_index}
    A heading is always a chunk of its own, and a chunk never spans one.
    """
    sentences_per_chunk = max(1, int(sentences_per_chunk))
    chunks = []
    current = []
    current_start = 0

    for index, (kind, text) in enumerate(units):
        if kind == "h":
            if current:
                chunks.append({"type": "text", "sentences": current, "start": current_start})
                current = []
            chunks.append({"type": "heading", "text": text, "start": index})
            continue
        if not current:
            current_start = index
        current.append(text)
        if len(current) >= sentences_per_chunk:
            chunks.append({"type": "text", "sentences": current, "start": current_start})
            current = []

    if current:
        chunks.append({"type": "text", "sentences": current, "start": current_start})
    return chunks


def chunk_index_for_position(chunks, position):
    """Index of the chunk that contains reading position `position`."""
    index = 0
    for i, chunk in enumerate(chunks):
        if chunk["start"] <= position:
            index = i
        else:
            break
    return index


def _cache_key(filepath):
    hasher = hashlib.sha256()
    size = os.path.getsize(filepath)
    hasher.update(f"v{CACHE_VERSION}:{size}".encode())
    with open(filepath, "rb") as f:
        hasher.update(f.read(65536))
        if size > 65536:
            f.seek(max(0, size - 65536))
            hasher.update(f.read(65536))
    return hasher.hexdigest()[:16]


def load_or_parse_book(filepath, cache_dir):
    """
    Return (title or None, units). Parsing is cached on disk per file, so
    a book is only parsed the first time it is opened.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, _cache_key(filepath) + ".json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("title"), data["units"]
        except (OSError, ValueError, KeyError):
            pass  # unreadable cache: parse again

    title, paragraphs = extract_book(filepath)
    units = paragraphs_to_units(paragraphs)

    try:
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"title": title, "units": units}, f)
        os.replace(tmp_path, cache_path)
    except OSError:
        pass  # caching is an optimisation only

    return title, units
