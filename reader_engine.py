"""
reader_engine.py

Handles:
- Extracting readable text from .epub and .pdf files
- Splitting text into sentences
- Grouping sentences into "chunks" (like dialogue/text boxes)

Deliberately dependency-light: EPUB parsing uses only the Python
standard library (zipfile + xml.etree.ElementTree + html.parser) rather
than ebooklib/BeautifulSoup/lxml. Those libraries pull in lxml as a
hard dependency, and lxml is a compiled C extension that's fragile to
cross-compile reliably via python-for-android's non-recipe pip install
path - it was the direct cause of an earlier build failure, and even
after removing it directly, it kept resurfacing indirectly through
ebooklib. Sticking to stdlib + pypdf (which has zero required
third-party dependencies for plain text extraction) avoids that whole
class of Android-packaging problem for good.
"""

import os
import re
import json
import hashlib
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from pypdf import PdfReader

# A conservative sentence splitter. Handles ., !, ? as terminators while
# trying not to split on common abbreviations (Mr., Mrs., Dr., e.g., etc.)
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "e.g", "i.e", "fig", "no", "vol", "cf", "approx",
}

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'])')

