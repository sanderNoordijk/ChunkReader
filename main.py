"""
ChunkReader - an EPUB/PDF reader that shows a book a few sentences at a
time - a few lines per screen, like a dialogue box in a video game.
"""

__version__ = "0.3.0"

import os
import threading
import time
import traceback

from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.logger import Logger
from kivy.metrics import dp, sp
from kivy.properties import (
    BooleanProperty, ListProperty, NumericProperty, ObjectProperty,
    StringProperty,
)
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.utils import escape_markup, platform

import reader_engine
from storage import DEFAULT_SETTINGS, LINE_CHOICES, THEMES, Storage

try:  # written by the GitHub Actions build, absent when run from source
    from build_info import BUILD
except Exception:
    BUILD = "dev"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
READING_FONT = "OpenDyslexic"
FALLBACK_FONT = "Roboto"   # ships with Kivy
MIN_FIT_FONT_SP = 12
SWIPE_DISTANCE_DP = 40

# Game mode: a blue window with a white border and white letters, in the
# style of 16-bit role-playing games.
RPG_BLUE_TOP = (0x3A, 0x58, 0xD8)
RPG_BLUE_BOTTOM = (0x08, 0x10, 0x60)

KEY_BACK = 27      # Android back button / Esc
KEY_RIGHT = 275
KEY_LEFT = 276
KEY_SPACE = 32


def hex_to_rgba(hex_color, alpha=1.0):
    hex_color = hex_color.lstrip("#")
    return [int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] + [alpha]


def wrapped_label(text, **kwargs):
    """A Label that wraps its text and grows to the height it needs."""
    label = Label(text=text, size_hint_y=None, halign="left", valign="top", **kwargs)
    label.bind(
        width=lambda inst, width: setattr(inst, "text_size", (width, None)),
        texture_size=lambda inst, size: setattr(inst, "height", size[1]),
    )
    return label


class LibraryScreen(Screen):
    pass


class ReaderScreen(Screen):
    pass


class SettingsScreen(Screen):
    pass


class ReaderArea(FloatLayout):
    """
    The reading area. Go to the next page by swiping left or by tapping
    anywhere on the right 70%; go back by swiping right or by tapping the
    left 30%.
    """

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            touch.ud["reader_start"] = touch.pos
            return True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        start = touch.ud.get("reader_start")
        if start is None:
            return super().on_touch_up(touch)
        app = App.get_running_app()
        dx, dy = touch.x - start[0], touch.y - start[1]
        limit = dp(SWIPE_DISTANCE_DP)
        if abs(dx) >= limit and abs(dx) > abs(dy):      # horizontal swipe
            if dx < 0:
                app.next_page()
            else:
                app.prev_page()
        elif abs(dx) < limit and abs(dy) < limit:        # tap
            if touch.x - self.x < self.width * 0.3:
                app.prev_page()
            else:
                app.next_page()
        return True


class BookRow(BoxLayout):
    book_id = StringProperty("")
    title = StringProperty("")
    progress_text = StringProperty("")


class ThemeButton(Button):
    theme_key = StringProperty("")
    selected = BooleanProperty(False)


class CrashGuard(ExceptionHandler):
    """
    Shows an unexpected error on screen instead of letting the app close.
    If errors keep coming in quick succession, the app is allowed to stop
    rather than loop forever.
    """

    def __init__(self, app):
        self.app = app
        self.recent = []

    def handle_exception(self, exception):
        now = time.time()
        self.recent = [t for t in self.recent if now - t < 3.0] + [now]
        if len(self.recent) > 3:
            return ExceptionManager.RAISE
        details = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__))
        Logger.error("ChunkReader: unexpected error\n" + details)
        self.app.remember_error(details)
        Clock.schedule_once(
            lambda dt: self.app.show_message("Something went wrong", details), 0)
        return ExceptionManager.PASS


class ChunkReaderApp(App):
    # Properties the .kv layout binds to.
    bg_color = ListProperty([0, 0, 0, 1])
    fg_color = ListProperty([1, 1, 1, 1])
    accent_color = ListProperty([0.29, 0.56, 0.85, 1])
    font_name_value = StringProperty(FALLBACK_FONT)
    font_size_value = NumericProperty(DEFAULT_SETTINGS["font_size"])
    line_spacing_value = NumericProperty(DEFAULT_SETTINGS["line_spacing"])
    lines_per_page = NumericProperty(DEFAULT_SETTINGS["lines_per_page"])
    game_mode = BooleanProperty(False)
    rpg_texture = ObjectProperty(None, allownone=True)
    rpg_cursor_alpha = NumericProperty(1.0)
    version_text = StringProperty(f"ChunkReader {__version__} (build {BUILD})")

    # ------------------------------------------------------------------
    # Start-up
    # ------------------------------------------------------------------

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sm = None
        self.storage = None
        self.settings_data = dict(DEFAULT_SETTINGS)
        self.current_book = None
        self.current_units = []
        self.current_blocks = []
        self.block_index = 0
        self.page_index = 0
        self._page_cache = {}
        self._metrics = None
        self._layout_dirty = True
        self.settings_return_to = "library"
        self._startup_error = None
        self._busy_popup = None
        self._message_popup = None
        self._open_token = 0
        self._save_settings_trigger = Clock.create_trigger(self._save_settings_now, 0.4)

    def load_kv(self, filename=None):
        # A mistake in the layout file must not close the app silently.
        try:
            return super().load_kv(filename)
        except Exception:
            self._startup_error = traceback.format_exc()
            return False

    def build(self):
        self.title = "ChunkReader"
        try:
            if self._startup_error:
                raise RuntimeError("The layout file could not be loaded.")
            return self._build_ui()
        except Exception:
            details = (self._startup_error or "") + "\n" + traceback.format_exc()
            Logger.error("ChunkReader: start-up failed\n" + details)
            return self._build_error_screen(details)

    def _build_ui(self):
        self.storage = Storage(self.user_data_dir)
        self.settings_data = self.storage.load_settings()
        self._register_fonts()
        self._make_rpg_texture()
        self.apply_settings_to_properties()

        self.sm = ScreenManager(transition=NoTransition())
        self.sm.add_widget(LibraryScreen(name="library"))
        self.sm.add_widget(ReaderScreen(name="reader"))
        self.sm.add_widget(SettingsScreen(name="settings"))
        self._build_theme_buttons()

        ExceptionManager.add_handler(CrashGuard(self))
        Window.bind(on_keyboard=self._on_keyboard)
        Clock.schedule_interval(self._blink_rpg_cursor, 0.6)
        Clock.schedule_once(lambda dt: self.refresh_library(), 0)
        return self.sm

    def _build_error_screen(self, details):
        """Shown instead of a black screen if start-up fails."""
        scroll = ScrollView()
        label = wrapped_label(
            "ChunkReader could not start.\n\n"
            f"{self.version_text}\n\n"
            "Please send a screenshot of this screen:\n\n" + details,
            font_size=sp(13), padding=(dp(16), dp(16)))
        scroll.add_widget(label)
        return scroll

    def _register_fonts(self):
        regular = os.path.join(APP_DIR, "fonts", "OpenDyslexic-Regular.otf")
        bold = os.path.join(APP_DIR, "fonts", "OpenDyslexic-Bold.otf")
        try:
            if not os.path.exists(regular):
                raise FileNotFoundError(regular)
            LabelBase.register(
                name=READING_FONT, fn_regular=regular,
                fn_bold=bold if os.path.exists(bold) else regular)
            self.font_name_value = READING_FONT
        except Exception:
            Logger.warning("ChunkReader: OpenDyslexic font unavailable, using default")
            self.font_name_value = FALLBACK_FONT

    def _make_rpg_texture(self):
        """A vertical blue gradient used as the game-mode window fill."""
        texture = Texture.create(size=(1, 64), colorfmt="rgb")
        texture.wrap = "clamp_to_edge"
        self._fill_rpg_texture(texture)
        # Android can drop textures while the app is in the background.
        texture.add_reload_observer(self._fill_rpg_texture)
        self.rpg_texture = texture

    @staticmethod
    def _fill_rpg_texture(texture):
        rows = bytearray()
        for row in range(64):   # the first row is drawn at the top
            t = row / 63.0
            rows.extend(int(a + (b - a) * t)
                        for a, b in zip(RPG_BLUE_TOP, RPG_BLUE_BOTTOM))
        texture.blit_buffer(bytes(rows), colorfmt="rgb", bufferfmt="ubyte")

    def _blink_rpg_cursor(self, dt):
        if self.game_mode and self.sm.current == "reader":
            self.rpg_cursor_alpha = 0.0 if self.rpg_cursor_alpha else 1.0

    def apply_settings_to_properties(self):
        s = self.settings_data
        theme = THEMES.get(s["theme"], THEMES[DEFAULT_SETTINGS["theme"]])
        self.bg_color = hex_to_rgba(theme["bg"])
        self.fg_color = hex_to_rgba(theme["fg"])
        self.accent_color = hex_to_rgba(theme["accent"])
        self.font_size_value = s["font_size"]
        self.line_spacing_value = s["line_spacing"]
        self.lines_per_page = s["lines_per_page"]
        self.game_mode = bool(s["game_mode"])
        Window.clearcolor = self.bg_color

    def on_pause(self):
        # Must be True, or the app closes when the system file picker opens.
        return True

    def on_resume(self):
        pass

    def remember_error(self, details):
        try:
            with open(os.path.join(self.user_data_dir, "last_error.txt"), "w",
                      encoding="utf-8") as f:
                f.write(details)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def show_message(self, title, text):
        if self._message_popup is not None:
            self._message_popup.dismiss()
        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        scroll = ScrollView()
        scroll.add_widget(wrapped_label(str(text), font_size=sp(15)))
        content.add_widget(scroll)
        ok_button = Button(text="OK", size_hint_y=None, height=dp(52), font_size=sp(16))
        content.add_widget(ok_button)
        popup = Popup(title=title, title_size=sp(17), content=content,
                      size_hint=(0.92, 0.6))
        ok_button.bind(on_release=lambda *_: popup.dismiss())
        popup.bind(on_dismiss=lambda *_: setattr(self, "_message_popup", None))
        self._message_popup = popup
        popup.open()

    def _show_busy(self, text):
        self._hide_busy()
        self._busy_popup = Popup(
            title="Please wait", title_size=sp(17), auto_dismiss=False,
            content=Label(text=text, font_size=sp(16)),
            size_hint=(0.8, None), height=dp(170))
        self._busy_popup.open()

    def _hide_busy(self):
        if self._busy_popup is not None:
            self._busy_popup.dismiss()
            self._busy_popup = None

    # ------------------------------------------------------------------
    # Library
    # ------------------------------------------------------------------

    def refresh_library(self):
        container = self.sm.get_screen("library").ids.book_list
        container.clear_widgets()
        books = self.storage.load_library()["books"]

        if not books:
            container.add_widget(wrapped_label(
                "No books yet.\n\nTap \"+ Add book\" to choose an EPUB or PDF "
                "file from your device.",
                font_name=self.font_name_value, font_size=sp(18),
                color=self.fg_color, padding=(dp(8), dp(24))))
            return

        for book in books:
            progress = ""
            total = book.get("total_units")
            if total:
                pct = round(100 * book.get("position", 0) / max(1, total - 1))
                progress = f"{min(100, pct)}%"
            container.add_widget(BookRow(
                book_id=book["id"], title=book["title"], progress_text=progress))

    def confirm_remove_book(self, book_id):
        book = self.storage.get_book(book_id)
        if book is None:
            return
        content = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(10))
        scroll = ScrollView()
        scroll.add_widget(wrapped_label(
            f"Remove \"{book['title']}\" from your library?\n\n"
            "The original file on your device is not deleted.", font_size=sp(16)))
        content.add_widget(scroll)
        buttons = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(10))
        cancel = Button(text="Cancel", font_size=sp(16))
        remove = Button(text="Remove", font_size=sp(16))
        buttons.add_widget(cancel)
        buttons.add_widget(remove)
        content.add_widget(buttons)
        popup = Popup(title="Remove book", title_size=sp(17), content=content,
                      size_hint=(0.9, 0.45))

        def do_remove(*_):
            popup.dismiss()
            if self.current_book and self.current_book["id"] == book_id:
                self.current_book = None
                self.current_units = []
                self.current_blocks = []
            self.storage.remove_book(book_id)
            self.refresh_library()

        cancel.bind(on_release=lambda *_: popup.dismiss())
        remove.bind(on_release=do_remove)
        popup.open()

    # ------------------------------------------------------------------
    # Adding a book
    # ------------------------------------------------------------------

    def prompt_add_book(self):
        if platform == "android":
            try:
                import android_picker
                android_picker.pick_document(self._on_android_pick)
            except Exception:
                self.show_message("Could not open the file picker",
                                  traceback.format_exc())
        else:
            self._open_desktop_filechooser()

    def _on_android_pick(self, uri_string):
        # Called on Android's UI thread: hand over to the Kivy thread.
        if uri_string:
            Clock.schedule_once(lambda dt: self._start_android_import(uri_string), 0)

    def _start_android_import(self, uri_string):
        self._show_busy("Adding book...")
        # One frame later, so the "please wait" box is drawn first.
        Clock.schedule_once(lambda dt: self._import_android_uri(uri_string), 0.1)

    def _import_android_uri(self, uri_string):
        incoming = os.path.join(self.storage.books_dir, "incoming.tmp")
        try:
            import android_picker
            name = android_picker.query_display_name(uri_string) or "Book"
            android_picker.copy_uri_to_path(uri_string, incoming)
            self.import_file(incoming, display_name=name)
        except Exception as exc:
            self.show_message("Could not add this file",
                              f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self._hide_busy()
            try:
                os.remove(incoming)
            except OSError:
                pass

    def import_file(self, filepath, display_name=None):
        """Add a file that is readable at `filepath` to the library."""
        name = display_name or os.path.basename(filepath)
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".epub", ".pdf"):
            ext = reader_engine.sniff_book_type(filepath)
        if ext is None:
            self.show_message(
                "Not a book file",
                f"\"{name}\" is not an EPUB or PDF file, so it cannot be added.")
            return None
        record = self.storage.import_book(filepath, display_name=name, ext=ext)
        self.refresh_library()
        return record

    def _open_desktop_filechooser(self):
        from kivy.uix.filechooser import FileChooserListView
        content = BoxLayout(orientation="vertical", spacing=dp(8))
        chooser = FileChooserListView(
            path=os.path.expanduser("~"), filters=["*.epub", "*.pdf"])
        content.add_widget(chooser)
        add_button = Button(text="Add selected file", size_hint_y=None, height=dp(52))
        content.add_widget(add_button)
        popup = Popup(title="Choose a book", content=content, size_hint=(0.95, 0.95))

        def do_import(*_):
            popup.dismiss()
            if chooser.selection:
                try:
                    self.import_file(chooser.selection[0])
                except Exception as exc:
                    self.show_message("Could not add this file", str(exc))

        add_button.bind(on_release=do_import)
        popup.open()

    # ------------------------------------------------------------------
    # Opening and reading a book
    # ------------------------------------------------------------------

    def open_book_by_id(self, book_id):
        book = self.storage.get_book(book_id)
        if book is None:
            self.refresh_library()
            return
        self._open_token += 1
        self._show_busy("Opening book...")
        # Parsing can take a while for a big PDF, so it runs off the UI thread.
        threading.Thread(
            target=self._parse_worker, args=(book, self._open_token), daemon=True
        ).start()

    def _parse_worker(self, book, token):
        title, units, error = None, None, None
        try:
            title, units = reader_engine.load_or_parse_book(
                self.storage.book_filepath(book), self.storage.cache_dir)
        except Exception as exc:
            Logger.error("ChunkReader: could not open book\n" + traceback.format_exc())
            error = str(exc) or exc.__class__.__name__
        Clock.schedule_once(
            lambda dt: self._on_book_parsed(book, title, units, error, token), 0)

    def _on_book_parsed(self, book, title, units, error, token):
        if token != self._open_token:
            return  # a newer request replaced this one
        self._hide_busy()
        if error:
            self.show_message("Could not open this book", error)
            return
        if not units:
            self.show_message("Could not open this book",
                              "No readable text was found in this file.")
            return

        updates = {"total_units": len(units)}
        if title and book.get("total_units") is None:
            updates["title"] = title   # first open: use the book's real title
        self.storage.update_book(book["id"], **updates)
        book.update(updates)

        self.current_book = book
        self.current_units = units
        self.current_blocks = reader_engine.build_blocks(units)
        self._layout_dirty = True
        self.sm.current = "reader"
        self.update_reader_view()

    # -- page layout -----------------------------------------------------

    def _reader_metrics(self):
        """Text size, line width and box size for the current screen."""
        area = self.sm.get_screen("reader").ids.reader_area
        pad = dp(22) if self.game_mode else dp(6)
        box_width = area.width * 0.94
        size = sp(self.font_size_value)
        floor = sp(MIN_FIT_FONT_SP)
        while True:   # shrink only if N lines would not fit on the screen
            probe = CoreLabel(
                text="\n".join(["Ag"] * int(self.lines_per_page)),
                font_name=self.font_name_value, font_size=size,
                line_height=self.line_spacing_value)
            probe.refresh()
            text_height = probe.texture.height
            if text_height + 2 * pad <= area.height * 0.96 or size <= floor:
                break
            size = max(floor, size * 0.93)
        return {
            "font_size": size, "pad": pad,
            "text_width": box_width - 2 * pad,
            "box_size": (box_width, text_height + 2 * pad),
        }

    def _measure_function(self, bold):
        core = CoreLabel(font_name=self.font_name_value,
                         font_size=self._metrics["font_size"], bold=bold)
        return lambda text: core.get_extents(text)[0]

    def _relayout(self):
        """Lay the book out again for the current settings and screen size."""
        self._metrics = self._reader_metrics()
        self._measure = {False: self._measure_function(False),
                         True: self._measure_function(True)}
        self._page_cache = {}
        unit = self.current_book.get("position", 0)
        word = self.current_book.get("position_word", 0)
        self.block_index = reader_engine.block_index_for_unit(self.current_blocks, unit)
        self.page_index = reader_engine.page_index_for_word(
            self._pages(self.block_index), unit, word)
        self._layout_dirty = False

    def _pages(self, block_index):
        """The pages of one block (laid out the first time it is needed)."""
        pages = self._page_cache.get(block_index)
        if pages is None:
            block = self.current_blocks[block_index]
            pages = reader_engine.paginate_block(
                self.current_units, block,
                self._measure[block["type"] == "heading"],
                self._metrics["text_width"], self.lines_per_page)
            self._page_cache[block_index] = pages
        return pages

    def on_reader_resized(self, *_):
        """The reading area changed size (first layout, or rotation)."""
        self._layout_dirty = True
        if self.sm is not None and self.sm.current == "reader":
            self.update_reader_view()

    def update_reader_view(self):
        if not self.current_book or not self.current_blocks:
            return
        ids = self.sm.get_screen("reader").ids
        area, box, label = ids.reader_area, ids.rpg_box, ids.chunk_label
        if area.width < dp(50) or area.height < dp(50):
            return   # not laid out yet; on_reader_resized calls again
        if self._layout_dirty:
            self._relayout()

        page = self._pages(self.block_index)[self.page_index]
        metrics = self._metrics
        text = "\n".join(escape_markup(line) for line in page["lines"])
        label.font_size = metrics["font_size"]
        label.halign = "center" if page["heading"] else "left"
        label.text = "[b]" + text + "[/b]" if page["heading"] else text
        label.texture_update()
        label.size = label.texture_size

        # The box always has room for the full number of lines, so it does
        # not jump around when a page has fewer lines.
        box.size = metrics["box_size"]
        box.center = area.center
        if page["heading"]:
            label.center = box.center
        else:
            label.x = box.x + metrics["pad"]
            label.top = box.top - metrics["pad"]

        last_block = self.block_index == len(self.current_blocks) - 1
        if last_block and self.page_index == len(self._pages(self.block_index)) - 1:
            fraction = 1.0
        else:
            fraction = page["unit"] / max(1, len(self.current_units) - 1)
        ids.progress_bar.value = fraction
        ids.progress_label.text = f"{round(fraction * 100)}%"
        ids.book_title_label.text = self.current_book["title"]

        if (self.current_book.get("position"), self.current_book.get("position_word")) \
                != (page["unit"], page["word"]):
            self.current_book["position"] = page["unit"]
            self.current_book["position_word"] = page["word"]
            self.storage.update_book(
                self.current_book["id"],
                position=page["unit"], position_word=page["word"])

    def next_page(self):
        if not self.current_blocks or self._layout_dirty:
            return
        if self.page_index < len(self._pages(self.block_index)) - 1:
            self.page_index += 1
        elif self.block_index < len(self.current_blocks) - 1:
            self.block_index += 1
            self.page_index = 0
        else:
            return   # end of the book
        self.update_reader_view()

    def prev_page(self):
        if not self.current_blocks or self._layout_dirty:
            return
        if self.page_index > 0:
            self.page_index -= 1
        elif self.block_index > 0:
            self.block_index -= 1
            self.page_index = len(self._pages(self.block_index)) - 1
        else:
            return   # start of the book
        self.update_reader_view()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def go_to_library(self):
        self.sm.current = "library"
        self.refresh_library()

    def go_to_settings(self):
        self.settings_return_to = self.sm.current
        self.sm.current = "settings"

    def go_back_from_settings(self):
        if self.settings_return_to == "reader" and self.current_blocks:
            self.sm.current = "reader"
            self.update_reader_view()
        else:
            self.go_to_library()

    def _on_keyboard(self, window, key, *args):
        current = self.sm.current
        if key == KEY_BACK:
            if self._message_popup is not None:
                self._message_popup.dismiss()
                return True
            if current == "reader":
                self.go_to_library()
                return True
            if current == "settings":
                self.go_back_from_settings()
                return True
            return False   # on the library screen: leave the app
        if current == "reader":
            if key in (KEY_RIGHT, KEY_SPACE):
                self.next_page()
                return True
            if key == KEY_LEFT:
                self.prev_page()
                return True
        return False

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _build_theme_buttons(self):
        container = self.sm.get_screen("settings").ids.theme_list
        container.clear_widgets()
        for key, theme in THEMES.items():
            button = ThemeButton(
                text=theme["label"], theme_key=key,
                background_color=hex_to_rgba(theme["bg"]),
                color=hex_to_rgba(theme["fg"]),
                selected=(key == self.settings_data["theme"]))
            button.bind(on_release=lambda inst: self.set_theme(inst.theme_key))
            container.add_widget(button)

    def _save_settings_now(self, *_):
        self.storage.save_settings(self.settings_data)

    def set_theme(self, theme_key):
        if theme_key not in THEMES:
            return
        self.settings_data["theme"] = theme_key
        self.apply_settings_to_properties()
        for button in self.sm.get_screen("settings").ids.theme_list.children:
            button.selected = (button.theme_key == theme_key)
        self._save_settings_trigger()

    # Each of these changes how the pages are laid out; the reader lays the
    # book out again (keeping your place) the next time it is shown.

    def toggle_game_mode(self):
        self.game_mode = not self.game_mode
        self.settings_data["game_mode"] = self.game_mode
        self._layout_dirty = True
        self._save_settings_trigger()

    def set_lines_per_page(self, value):
        value = int(round(value))
        if value not in LINE_CHOICES or value == self.settings_data["lines_per_page"]:
            return
        self.settings_data["lines_per_page"] = value
        self.lines_per_page = value
        self._layout_dirty = True
        self._save_settings_trigger()

    def set_font_size(self, value):
        value = int(round(value))
        if value == self.settings_data["font_size"]:
            return
        self.settings_data["font_size"] = value
        self.font_size_value = value
        self._layout_dirty = True
        self._save_settings_trigger()

    def set_line_spacing(self, value):
        value = round(float(value), 1)
        if value == self.settings_data["line_spacing"]:
            return
        self.settings_data["line_spacing"] = value
        self.line_spacing_value = value
        self._layout_dirty = True
        self._save_settings_trigger()


if __name__ == "__main__":
    ChunkReaderApp().run()
