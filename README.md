# ChunkReader

An EPUB/PDF reader for people with dyslexia or other reading difficulties.
Instead of a full page, a book is shown a few lines at a time in a
large, clear font - like a dialogue box in a video game.

## Features

- Add your own `.epub` and `.pdf` files (kept on the device only)
- 2, 3 or 5 lines of text at a time
- OpenDyslexic font, adjustable text size and line spacing
- Five colour themes, plus **game mode**: a blue text box with a white
  border and white letters, in the style of 16-bit role-playing games
- Remembers where you stopped in every book
- Swipe, or tap the right side of the screen, for the next lines; tap
  the left side to go back
- Fully offline: no account, no permissions, no tracking

## Files

| File | What it does |
| --- | --- |
| `main.py` | The app: screens, navigation, settings |
| `chunkreader.kv` | The layout of the screens |
| `reader_engine.py` | Reads EPUB/PDF files and lays the text out as pages of a few lines |
| `storage.py` | Saves the library, reading positions and settings |
| `android_picker.py` | Android file picker (Storage Access Framework) |
| `selfcheck.py` | Run by the build first; stops it if a file is incomplete or misplaced |
| `buildozer.spec` | Android build settings (must be in the top folder) |
| `.github/workflows/build-apk.yml` | Builds the APK on every push to `main` |

## Getting the APK

Every push to `main` builds an APK. Open the repository's **Actions**
tab, open the latest run, and download the `chunkreader-apk-build<N>`
artifact. The same build number is shown at the bottom of the app's
Settings screen, so you can check which build is installed.

## Running on a computer

```bash
pip install -r requirements.txt
python main.py
```

## Changing the version

Edit `__version__` at the top of `main.py`; the build reads it from there.

## Font licence

OpenDyslexic is bundled under the SIL Open Font License (see `fonts/OFL.txt`).
