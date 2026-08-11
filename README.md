# ChunkReader

An EPUB/PDF reader built for dyslexia and other reading difficulties.
Instead of a full page of text, books are shown a few sentences at a
time in a large-font, high-contrast "textbox" — similar to dialogue
boxes in games like Phoenix Wright or Danganronpa.

## Features (v1)

- Import your own `.epub` and `.pdf` files (stored locally on-device only)
- Text shown in small chunks (1–5 sentences, adjustable) instead of full pages
- Large, dyslexia-friendly font (bundled: [OpenDyslexic](https://github.com/antijingoist/opendyslexic), SIL OFL licensed)
- Adjustable font size and line spacing
- 5 color themes, including black/white but also cream-on-black,
  navy/off-white, and high-contrast yellow (pure white-on-black can cause
  glare/halation for some readers, so alternatives are offered)
- Bookmarks / auto-resume: reopening a book returns you to your last chunk
- Tap zones to advance: tap right two-thirds of the screen to go forward,
  left third to go back — no swipe gestures required
- Fully offline, no account, no analytics

## Project structure

```
ChunkReader/
├── main.py                 # App entry point, screen logic
├── chunkreader.kv          # UI layout (Kivy language)
├── reader_engine.py        # EPUB/PDF parsing + sentence/chunk splitting
├── storage.py               # Local library + settings persistence
├── buildozer.spec           # Android build configuration
├── requirements.txt         # Desktop dev dependencies
├── fonts/                   # Bundled OpenDyslexic font + license
├── assets/                   # App icon
└── .github/workflows/
    └── build-apk.yml         # CI: builds a debug APK on every push to main
```

## Testing on your desktop first (recommended before building the APK)

Buildozer/Android builds are slow to iterate on, so test the app logic
on your desktop first:

```bash
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 main.py
```

A window will open. File picking on desktop falls back to a built-in
Kivy file browser (on Android it uses the native picker via `plyer`).

## Building the APK

### Option A — GitHub Actions (recommended, matches your Setlistify workflow)

1. Push this project to a new GitHub repo (see below).
2. The workflow in `.github/workflows/build-apk.yml` runs automatically
   on every push to `main`.
3. Go to the repo's **Actions** tab → the latest run → download the
   `chunkreader-apk` artifact. Unzip it to get the `.apk`.
4. To install: copy the APK to your Android device and open it (you may
   need to allow "install unknown apps" for whichever app you use to
   open the file).

You can also trigger a build manually from the **Actions** tab using
"Run workflow" (this is enabled via `workflow_dispatch` in the YAML).

### Option B — Build locally with Buildozer

Only really practical on Linux (or WSL2 on Windows):

```bash
pip install buildozer cython
buildozer android debug
```

The APK will land in `bin/`. First build downloads the Android SDK/NDK
and will take a while.

## Pushing this to GitHub

From inside the `ChunkReader` folder:

```bash
git init
git add .
git commit -m "Initial ChunkReader scaffold"
git branch -M main
git remote add origin https://github.com/<your-username>/ChunkReader.git
git push -u origin main
```

If you have the GitHub CLI installed, you can create the repo in one step:

```bash
gh repo create ChunkReader --public --source=. --remote=origin --push
```

## Roadmap / not yet built

These were discussed but intentionally left out of v1 to keep scope tight:

- Text-to-speech read-aloud (Kivy makes word-level highlight sync fiddly —
  worth revisiting if this becomes a priority)
- "Bionic reading" style (bold word-starts) toggle
- Table of contents / chapter jump list (currently headings appear inline
  as their own chunk, but there's no jump-to-chapter menu yet)

## Notes on the sentence splitter

`reader_engine.py` uses a regex-based sentence splitter rather than a
heavier NLP library (like `nltk`), specifically to avoid pulling in
large models or C-extension dependencies that tend to complicate
Buildozer/python-for-android builds. It handles common abbreviations
(Mr., Dr., etc.) reasonably well but isn't perfect — if you run into a
book where chunking looks visibly wrong, that's the file to look at.
