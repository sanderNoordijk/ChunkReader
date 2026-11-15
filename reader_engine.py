"""
reader_engine.py

Turns an .epub or .pdf into a flat list of reading "units" (headings and
sentences), and lays that text out as pages of a few lines each.

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


# ---------------------------------------------------------------------
# Pages: a few LINES of text at a time
# ---------------------------------------------------------------------
#
# The book is cut into "blocks" (a heading, or a run of sentences). Each
# block is wrapped into lines that fit the screen and cut into pages of
# at most N lines, ending at a natural pause where possible. Blocks are laid out one at a time, only when they are needed,
# so opening a long book stays instant.

def build_blocks(units, max_sentences=40):
    """
    Split the units into blocks:
        {"type": "heading", "start": i, "end": i + 1}
        {"type": "text", "start": i, "end": j}      (sentences i .. j-1)
    A block of text holds at most `max_sentences` sentences.
    """
    blocks = []
    start = None
    for index, (kind, _text) in enumerate(units):
        if kind == "h":
            if start is not None:
                blocks.append({"type": "text", "start": start, "end": index})
                start = None
            blocks.append({"type": "heading", "start": index, "end": index + 1})
        elif start is None:
            start = index
        elif index - start >= max_sentences:
            blocks.append({"type": "text", "start": start, "end": index})
            start = index
    if start is not None:
        blocks.append({"type": "text", "start": start, "end": len(units)})
    return blocks


def _split_wide_word(word, measure, max_width):
    """Cut a word that is wider than a whole line into pieces that fit."""
    pieces = []
    piece = ""
    for char in word:
        if piece and measure(piece + char) > max_width:
            pieces.append(piece)
            piece = char
        else:
            piece += char
    if piece:
        pieces.append(piece)
    return pieces


def _block_tokens(units, block, measure, max_width):
    """
    The words of a block as tokens: (text, unit_index, word_index, ends_sentence).
    """
    tokens = []
    for unit_index in range(block["start"], block["end"]):
        words = units[unit_index][1].split()
        for word_index, word in enumerate(words):
            ends_sentence = word_index == len(words) - 1
            if len(word) > 6 and measure(word) > max_width:
                pieces = _split_wide_word(word, measure, max_width)
                for k, piece in enumerate(pieces):
                    tokens.append((piece, unit_index, word_index,
                                   ends_sentence and k == len(pieces) - 1))
            else:
                tokens.append((word, unit_index, word_index, ends_sentence))
    return tokens


def _take_lines(tokens, first, measure, max_width, max_lines):
    """
    Wrap tokens, starting at index `first`, into at most `max_lines` lines
    no wider than `max_width`. Returns the lines as lists of token indexes.
    """
    lines = []
    current = []
    text = ""
    for index in range(first, len(tokens)):
        word = tokens[index][0]
        candidate = text + " " + word if current else word
        if current and measure(candidate) > max_width:
            lines.append(current)
            if len(lines) == max_lines:
                return lines
            current, candidate = [], word
        current.append(index)
        text = candidate
    if current:
        lines.append(current)
    return lines


# A page may stop early at a natural pause, so that reading stops where
# the sentence does - but only if the page is reasonably full by then.
_SENTENCE_END_FILL = 0.4    # stop at the end of a sentence if >= 40% full
_PAUSE_FILL = 0.55          # stop at a comma etc. if >= 55% full
_PAUSE_MARKS = (",", ";", ":", "\u2014", "\u2013")
_CLOSERS = "\"'\u201d\u2019)]"


def _choose_page_end(tokens, first, last):
    """
    Given a full page holding tokens first..last, return the index of the
    token the page should really end on.
    """
    if last >= len(tokens) - 1:
        return last
    count = last - first + 1

    def full_enough(index, fill):
        return index - first + 1 >= count * fill

    # 1. The end of a sentence.
    for index in range(last, first - 1, -1):
        if tokens[index][3]:
            if full_enough(index, _SENTENCE_END_FILL):
                return index
            break

    # 2. A pause within the sentence. Be more willing to stop there if the
    #    sentence would otherwise spill just one or two words onto the
    #    next page.
    spills = any(tokens[i][3] for i in range(last + 1, min(last + 3, len(tokens))))
    fill = _SENTENCE_END_FILL if spills else _PAUSE_FILL
    for index in range(last, first - 1, -1):
        if not full_enough(index, fill):
            break
        if tokens[index][0].rstrip(_CLOSERS).endswith(_PAUSE_MARKS):
            return index

    # 3. No good place to stop: use the whole page.
    return last


def paginate_block(units, block, measure, max_width, lines_per_page):
    """
    Lay out one block as pages of at most `lines_per_page` lines:
        {"lines": ["...", "..."], "unit": u, "word": w, "heading": bool}
    (unit, word) is where the page starts: word `w` of unit `u`.

    measure: function(text) -> width, in the same unit as max_width.
    """
    lines_per_page = max(1, int(lines_per_page))
    is_heading = block["type"] == "heading"
    tokens = _block_tokens(units, block, measure, max_width)

    pages = []
    first = 0
    while first < len(tokens):
        lines = _take_lines(tokens, first, measure, max_width, lines_per_page)
        end = _choose_page_end(tokens, first, lines[-1][-1])
        page_lines = []
        for line in lines:
            kept = [tokens[i][0] for i in line if i <= end]
            if kept:
                page_lines.append(" ".join(kept))
        pages.append({"lines": page_lines, "unit": tokens[first][1],
                      "word": tokens[first][2], "heading": is_heading})
        first = end + 1

    if not pages:   # a block without any words
        pages.append({"lines": [""], "unit": block["start"], "word": 0,
                      "heading": is_heading})
    return pages


def list_chapters(units):
    """The headings of a book: [(unit_index, title), ...]."""
    return [(index, text) for index, (kind, text) in enumerate(units) if kind == "h"]


def block_index_for_unit(blocks, unit_index):
    """Index of the block that contains unit `unit_index`."""
    index = 0
    for i, block in enumerate(blocks):
        if block["start"] <= unit_index:
            index = i
        else:
            break
    return index


def page_index_for_word(pages, unit_index, word_index):
    """Index of the page (within one block) that contains the given word."""
    index = 0
    for i, page in enumerate(pages):
        if (page["unit"], page["word"]) <= (unit_index, word_index):
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
