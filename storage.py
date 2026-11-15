"""
storage.py

Local-only persistence (no accounts, no network) for:
- the library: imported books + where you stopped reading in each
- settings: theme, font size, line spacing, sentences per chunk

Everything lives in the app's private data folder on the device.
"""

import json
import os
import shutil
import uuid

LIBRARY_FILENAME = "library.json"
SETTINGS_FILENAME = "settings.json"
BOOKS_SUBDIR = "books"
CACHE_SUBDIR = "chunk_cache"

DEFAULT_SETTINGS = {
    "sentences_per_chunk": 3,
    "font_size": 26,          # in sp: scales with the screen and the
                              # system font-size setting
    "line_spacing": 1.4,
    "theme": "classic_bw",    # a key of THEMES
    "game_mode": False,       # blue RPG-style text box
}

# Pure white on black causes glare ("halation") for some readers, so
# softer alternatives are offered next to it.
CHUNK_SIZES = (2, 3, 5)   # the sentence counts the reader offers

THEMES = {
    "classic_bw": {"label": "Black & White", "bg": "#000000", "fg": "#FFFFFF", "accent": "#4A90D9"},
    "cream_black": {"label": "Cream on Black", "bg": "#000000", "fg": "#F5E6C8", "accent": "#E0B15A"},
    "navy_offwhite": {"label": "Navy & Off-white", "bg": "#0B1E33", "fg": "#F0EAD6", "accent": "#F2C14E"},
    "high_contrast_yellow": {"label": "Black & Yellow", "bg": "#000000", "fg": "#FFD400", "accent": "#FFFFFF"},
    "sepia": {"label": "Soft Sepia", "bg": "#2B2117", "fg": "#E8D9B5", "accent": "#C97B4A"},
}


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    """Write via a temp file so a crash mid-write cannot corrupt the file."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


class Storage:
    def __init__(self, user_data_dir):
        self.root = user_data_dir
        self.books_dir = os.path.join(self.root, BOOKS_SUBDIR)
        self.cache_dir = os.path.join(self.root, CACHE_SUBDIR)
        os.makedirs(self.books_dir, exist_ok=True)
        os.makedirs(self.cache_dir, exist_ok=True)
        self._library_path = os.path.join(self.root, LIBRARY_FILENAME)
        self._settings_path = os.path.join(self.root, SETTINGS_FILENAME)

    # ---------- Settings ----------

    def load_settings(self):
        settings = dict(DEFAULT_SETTINGS)
        stored = _read_json(self._settings_path, {})
        if isinstance(stored, dict):
            settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
        if settings["sentences_per_chunk"] not in CHUNK_SIZES:
            settings["sentences_per_chunk"] = DEFAULT_SETTINGS["sentences_per_chunk"]
        if settings["theme"] not in THEMES:
            settings["theme"] = DEFAULT_SETTINGS["theme"]
        return settings

    def save_settings(self, settings):
        _write_json(self._settings_path, settings)

    # ---------- Library ----------

    def load_library(self):
        library = _read_json(self._library_path, None)
        if not isinstance(library, dict) or not isinstance(library.get("books"), list):
            return {"books": []}
        return library

    def save_library(self, library):
        _write_json(self._library_path, library)

    def get_book(self, book_id):
        for book in self.load_library()["books"]:
            if book["id"] == book_id:
                return book
        return None

    def new_book_path(self, ext):
        """Reserve a file path inside the app's storage for a new book."""
        book_id = uuid.uuid4().hex[:12]
        return book_id, os.path.join(self.books_dir, book_id + ext)

    def register_book(self, book_id, stored_path, title):
        record = {
            "id": book_id,
            "filename": os.path.basename(stored_path),
            "title": title,
            "position": 0,        # index of the reading unit you are at
            "total_units": None,  # filled in the first time the book opens
        }
        library = self.load_library()
        library["books"].append(record)
        self.save_library(library)
        return record

    def import_book(self, source_filepath, display_name=None, ext=None):
        """Copy a file into the app's storage and add it to the library."""
        name = display_name or os.path.basename(source_filepath)
        ext = (ext or os.path.splitext(name)[1]).lower()
        if ext not in (".epub", ".pdf"):
            raise ValueError("Only EPUB and PDF files can be added.")
        book_id, dest_path = self.new_book_path(ext)
        shutil.copyfile(source_filepath, dest_path)
        title = os.path.splitext(name)[0] or "Untitled"
        return self.register_book(book_id, dest_path, title)

    def update_book(self, book_id, **fields):
        library = self.load_library()
        for book in library["books"]:
            if book["id"] == book_id:
                book.update(fields)
                break
        self.save_library(library)

    def remove_book(self, book_id):
        library = self.load_library()
        remaining = []
        for book in library["books"]:
            if book["id"] == book_id:
                try:
                    os.remove(os.path.join(self.books_dir, book["filename"]))
                except OSError:
                    pass
            else:
                remaining.append(book)
        library["books"] = remaining
        self.save_library(library)

    def book_filepath(self, book_record):
        return os.path.join(self.books_dir, book_record["filename"])
