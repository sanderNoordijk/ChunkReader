"""
ChunkReader - an epub/pdf reader that presents text in small,
video-game-dialogue-style chunks for easier reading.
"""

import os

from kivy.app import App
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.properties import StringProperty, NumericProperty, ListProperty, BooleanProperty
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.uix.popup import Popup
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button

from storage import Storage, THEMES, DEFAULT_SETTINGS
import reader_engine

# Android-only imports guarded so this still runs on desktop for testing.
try:
    from plyer import filechooser
except Exception:
    filechooser = None

ANDROID = os.environ.get("ANDROID_ARGUMENT") is not None
if ANDROID:
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.READ_EXTERNAL_STORAGE,
            Permission.WRITE_EXTERNAL_STORAGE,
        ])
    except Exception:
        pass


def hex_to_rgba(hex_color, alpha=1.0):
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16) / 255.0
    g = int(hex_color[2:4], 16) / 255.0
    b = int(hex_color[4:6], 16) / 255.0
    return [r, g, b, alpha]


class LibraryScreen(Screen):
    pass


class ReaderScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


class ConfirmPopupContent(BoxLayout):
    pass


class ChunkReaderApp(App):
    # Reactive properties bound to in .kv files
    bg_color = ListProperty([0, 0, 0, 1])
    fg_color = ListProperty([1, 1, 1, 1])
    accent_color = ListProperty([0.29, 0.56, 0.85, 1])
    font_size_value = NumericProperty(32)
    line_spacing_value = NumericProperty(1.5)
    font_name_value = StringProperty("OpenDyslexic")
    sentences_per_chunk = NumericProperty(3)

    current_book = None          # library record dict
    current_chunks = ListProperty([])
    current_chunk_index = NumericProperty(0)

    def build(self):
        self.title = "ChunkReader"
        self.storage = Storage(self.user_data_dir)
        self.settings_data = self.storage.load_settings()

        self._register_fonts()
        self.apply_settings_to_properties()

        self.sm = ScreenManager(transition=SlideTransition())
        self.sm.add_widget(LibraryScreen(name="library"))
        self.sm.add_widget(ReaderScreen(name="reader"))
        self.sm.add_widget(SettingsScreen(name="settings"))

        Clock.schedule_once(lambda dt: self.refresh_library(), 0)
        return self.sm

    def _register_fonts(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        regular = os.path.join(base_dir, "fonts", "OpenDyslexic-Regular.otf")
        bold = os.path.join(base_dir, "fonts", "OpenDyslexic-Bold.otf")
        if os.path.exists(regular):
            LabelBase.register(name="OpenDyslexic", fn_regular=regular,
                                fn_bold=bold if os.path.exists(bold) else regular)

    def apply_settings_to_properties(self):
        s = self.settings_data
        theme = THEMES.get(s.get("theme", "classic_bw"), THEMES["classic_bw"])
        self.bg_color = hex_to_rgba(theme["bg"])
        self.fg_color = hex_to_rgba(theme["fg"])
        self.accent_color = hex_to_rgba(theme["accent"])
        self.font_size_value = s.get("font_size", 32)
        self.line_spacing_value = s.get("line_spacing", 1.5)
        self.sentences_per_chunk = s.get("sentences_per_chunk", 3)
        font_choice = s.get("font", "opendyslexic")
        self.font_name_value = "OpenDyslexic" if font_choice == "opendyslexic" else "Roboto"
        if Window:
            Window.clearcolor = self.bg_color

    def save_settings(self):
        self.storage.save_settings(self.settings_data)

    # ---------------- Library ----------------

    def refresh_library(self):
        library = self.storage.load_library()
        screen = self.sm.get_screen("library")
        container = screen.ids.book_list
        container.clear_widgets()

        if not library["books"]:
            container.add_widget(Label(
                text="No books yet.\nTap '+ Add Book' to import an EPUB or PDF.",
                font_name=self.font_name_value,
                font_size=self.font_size_value * 0.6,
                color=self.fg_color,
                halign="center",
                size_hint_y=None,
                height=120,
            ))
            return

        for book in library["books"]:
            row = self._build_library_row(book)
            container.add_widget(row)

    def _build_library_row(self, book):
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=88, spacing=10, padding=(10, 6))

        progress_text = ""
        if book.get("total_chunks"):
            pct = int(100 * book["current_chunk_index"] / max(1, book["total_chunks"]))
            progress_text = f"  ({pct}% read)"

        btn = Button(
            text=f"{book['title']}{progress_text}",
            font_name=self.font_name_value,
            font_size=self.font_size_value * 0.5,
            halign="left",
            valign="middle",
            background_normal="",
            background_color=(0.15, 0.15, 0.15, 1),
            color=self.fg_color,
        )
        btn.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        btn.bind(on_release=lambda inst, b=book: self.open_book(b))

        remove_btn = Button(
            text="✕",
            font_size=self.font_size_value * 0.5,
            size_hint_x=None,
            width=60,
            background_normal="",
            background_color=(0.4, 0.1, 0.1, 1),
            color=(1, 1, 1, 1),
        )
        remove_btn.bind(on_release=lambda inst, b=book: self.confirm_remove_book(b))

        row.add_widget(btn)
        row.add_widget(remove_btn)
        return row

    def confirm_remove_book(self, book):
        content = BoxLayout(orientation="vertical", spacing=10, padding=10)
        content.add_widget(Label(text=f"Remove '{book['title']}' from your library?"))
        btn_row = BoxLayout(size_hint_y=None, height=50, spacing=10)
        popup = Popup(title="Confirm removal", content=content, size_hint=(0.8, 0.4))

        def do_remove(*_):
            self.storage.remove_book(book["id"])
            self.refresh_library()
            popup.dismiss()

        cancel_btn = Button(text="Cancel")
        cancel_btn.bind(on_release=lambda *_: popup.dismiss())
        confirm_btn = Button(text="Remove")
        confirm_btn.bind(on_release=do_remove)

        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(confirm_btn)
        content.add_widget(btn_row)
        popup.open()

    def prompt_add_book(self):
        if filechooser is not None:
            try:
                filechooser.open_file(
                    on_selection=self._on_file_chosen,
                    filters=["*.epub", "*.pdf"],
                )
                return
            except Exception:
                pass
        # Desktop fallback: simple Kivy filechooser popup
        self._open_desktop_filechooser()

    def _on_file_chosen(self, selection):
        if not selection:
            return
        filepath = selection[0]
        self._import_and_refresh(filepath)

    def _open_desktop_filechooser(self):
        from kivy.uix.filechooser import FileChooserListView
        content = BoxLayout(orientation="vertical")
        chooser = FileChooserListView(filters=["*.epub", "*.pdf"])
        content.add_widget(chooser)
        btn = Button(text="Import selected file", size_hint_y=None, height=50)
        popup = Popup(title="Choose a book", content=content, size_hint=(0.9, 0.9))

        def do_import(*_):
            if chooser.selection:
                self._import_and_refresh(chooser.selection[0])
            popup.dismiss()

        btn.bind(on_release=do_import)
        content.add_widget(btn)
        popup.open()

    def _import_and_refresh(self, filepath):
        try:
            self.storage.import_book(filepath)
        except Exception as e:
            self._show_error(f"Could not import file:\n{e}")
            return
        self.refresh_library()

    def _show_error(self, message):
        popup = Popup(title="Error", size_hint=(0.85, 0.4),
                       content=Label(text=message))
        popup.open()

    # ---------------- Reader ----------------

    def open_book(self, book_record):
        self.current_book = book_record
        try:
            title, chunks = reader_engine.load_or_build_chunks(
                self.storage.book_filepath(book_record),
                self.storage.cache_dir,
                sentences_per_chunk=self.sentences_per_chunk,
            )
        except Exception as e:
            self._show_error(f"Could not open book:\n{e}")
            return

        self.current_chunks = chunks
        self.current_chunk_index = min(
            book_record.get("current_chunk_index", 0),
            max(0, len(chunks) - 1),
        )
        if book_record.get("total_chunks") != len(chunks):
            self.storage.update_progress(book_record["id"], self.current_chunk_index, len(chunks))

        self.sm.current = "reader"
        self.update_reader_view()

    def update_reader_view(self):
        screen = self.sm.get_screen("reader")
        if not self.current_chunks:
            screen.ids.chunk_label.text = "(This book appears to be empty.)"
            screen.ids.progress_label.text = ""
            return

        chunk = self.current_chunks[self.current_chunk_index]
        if chunk["type"] == "heading":
            screen.ids.chunk_label.text = f"[b]{chunk['text']}[/b]"
        else:
            screen.ids.chunk_label.text = "\n\n".join(chunk["sentences"])

        total = len(self.current_chunks)
        screen.ids.progress_label.text = f"{self.current_chunk_index + 1} / {total}"
        screen.ids.book_title_label.text = self.current_book["title"] if self.current_book else ""

        self._persist_progress()

    def _persist_progress(self):
        if self.current_book:
            self.storage.update_progress(
                self.current_book["id"],
                self.current_chunk_index,
                len(self.current_chunks),
            )

    def next_chunk(self):
        if self.current_chunk_index < len(self.current_chunks) - 1:
            self.current_chunk_index += 1
            self.update_reader_view()

    def prev_chunk(self):
        if self.current_chunk_index > 0:
            self.current_chunk_index -= 1
            self.update_reader_view()

    def go_to_library(self):
        self.sm.current = "library"
        self.refresh_library()

    def go_to_settings(self):
        self.settings_return_to = self.sm.current
        self.sm.current = "settings"

    def go_back_from_settings(self):
        self.sm.current = getattr(self, "settings_return_to", "library")
        if self.sm.current == "reader":
            self.update_reader_view()
        else:
            self.refresh_library()

    # ---------------- Settings ----------------

    def set_theme(self, theme_key):
        self.settings_data["theme"] = theme_key
        self.apply_settings_to_properties()
        self.save_settings()
        if self.sm.current == "reader":
            self.update_reader_view()

    def set_sentences_per_chunk(self, value):
        value = int(value)
        self.settings_data["sentences_per_chunk"] = value
        self.sentences_per_chunk = value
        self.save_settings()
        # Re-open current book with new chunk size if one is open
        if self.current_book:
            self.open_book(self.current_book)

    def set_font_size(self, value):
        self.settings_data["font_size"] = int(value)
        self.font_size_value = int(value)
        self.save_settings()

    def set_line_spacing(self, value):
        self.settings_data["line_spacing"] = round(float(value), 2)
        self.line_spacing_value = round(float(value), 2)
        self.save_settings()


if __name__ == "__main__":
    ChunkReaderApp().run()
