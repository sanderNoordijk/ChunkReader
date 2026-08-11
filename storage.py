"""
storage.py

Local-only persistence (no accounts, no network) for:
- Library: list of imported books + reading progress
- Settings: theme, font size, spacing, chunk size

Everything lives under the Kivy App's user_data_dir so it survives
app updates and is sandboxed per-app on Android.
"""

import os
import json
import shutil
import uuid

LIBRARY_FILENAME = "library.json"
SETTINGS_FILENAME = "settings.json"
BOOKS_SUBDIR = "books"
CACHE_SUBDIR = "chunk_cache"

DEFAULT_SETTINGS = {
    "sentences_per_chunk": 3,
    "font_size": 32,
    "line_spacing": 1.5,
    "theme": "classic_bw",  # see THEMES below
    "font": "opendyslexic",  # "opendyslexic" or "default"
}
# Note: TTS and bionic-reading were discussed but de-scoped from v1 in
# favor of bookmarks/resume + custom themes. The chunk/settings data model
# leaves room to add them later without a migration.

# Dyslexia-conscious palettes. Pure white-on-black causes "halation"
# glare for some readers, so cream/soft tones are offered as alternatives.
THEMES = {
    "classic_bw": {"label": "Black & White", "bg": "#000000", "fg": "#FFFFFF", "accent": "#4A90D9"},
    "cream_black": {"label": "Cream on Black", "bg": "#000000", "fg": "#F5E6C8", "accent": "#4A90D9"},
    "navy_offwhite": {"label": "Navy & Off-white", "bg": "#0B1E33", "fg": "#F0EAD6", "accent": "#F2C14E"},
    "high_contrast_yellow": {"label": "Black & Yellow", "bg": "#000000", "fg": "#FFD400", "accent": "#FFFFFF"},
    "sepia": {"label": "Soft Sepia", "bg": "#2B2117", "fg": "#E8D9B5", "accent": "#C97B4A"},
}


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
        if os.path.exists(self._settings_path):
            with open(self._settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        return dict(DEFAULT_SETTINGS)

    def save_settings(self, settings):
        with open(self._settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)

    # ---------- Library ----------

    def load_library(self):
        if os.path.exists(self._library_path):
            with open(self._library_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"books": []}

    def save_library(self, library):
        with open(self._library_path, 'w', encoding='utf-8') as f:
            json.dump(library, f, indent=2)

    def import_book(self, source_filepath, display_title=None):
        """
        Copies the source file into the app's sandboxed storage and
        registers it in the library. Returns the new book record.
        """
        ext = os.path.splitext(source_filepath)[1].lower()
        if ext not in (".epub", ".pdf"):
            raise ValueError("Only .epub and .pdf files are supported.")

        book_id = uuid.uuid4().hex[:12]
        dest_filename = f"{book_id}{ext}"
        dest_path = os.path.join(self.books_dir, dest_filename)
        shutil.copyfile(source_filepath, dest_path)

        record = {
            "id": book_id,
            "filename": dest_filename,
            "title": display_title or os.path.splitext(os.path.basename(source_filepath))[0],
            "current_chunk_index": 0,
            "total_chunks": None,  # filled in once parsed
        }

        library = self.load_library()
        library["books"].append(record)
        self.save_library(library)
        return record

    def update_progress(self, book_id, chunk_index, total_chunks=None):
        library = self.load_library()
        for book in library["books"]:
            if book["id"] == book_id:
                book["current_chunk_index"] = chunk_index
                if total_chunks is not None:
                    book["total_chunks"] = total_chunks
                break
        self.save_library(library)

    def remove_book(self, book_id):
        library = self.load_library()
        remaining = []
        for book in library["books"]:
            if book["id"] == book_id:
                filepath = os.path.join(self.books_dir, book["filename"])
                if os.path.exists(filepath):
                    os.remove(filepath)
            else:
                remaining.append(book)
        library["books"] = remaining
        self.save_library(library)

    def book_filepath(self, book_record):
        return os.path.join(self.books_dir, book_record["filename"])
