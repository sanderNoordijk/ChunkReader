"""
selfcheck.py - run by the GitHub build BEFORE the 20-minute APK build.

It fails fast if a file in the repository is incomplete or in the wrong
place (for example a half-pasted file, or buildozer.spec saved in the
wrong folder), so a broken app is never built in the first place.

Run it yourself with:  python selfcheck.py
"""

import ast
import configparser
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
problems = []


def need(condition, message):
    if not condition:
        problems.append(message)


def functions_in(filename):
    path = os.path.join(ROOT, filename)
    if not os.path.exists(path):
        problems.append(f"{filename} is missing")
        return set()
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), filename)
    except SyntaxError as exc:
        problems.append(f"{filename} has a syntax error: {exc}")
        return set()
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


EXPECTED = {
    "main.py": {"ChunkReaderApp", "build", "open_book_by_id", "import_file",
                "update_reader_view", "fit_chunk_text", "set_theme"},
    "reader_engine.py": {"split_sentences", "extract_book", "build_chunks",
                         "load_or_parse_book", "chunk_index_for_position"},
    "storage.py": {"Storage", "import_book", "update_book", "remove_book"},
    "android_picker.py": {"pick_document", "copy_uri_to_path"},
}
for filename, names in EXPECTED.items():
    missing = names - functions_in(filename)
    need(not missing, f"{filename} is incomplete, missing: {sorted(missing)}")

kv_path = os.path.join(ROOT, "chunkreader.kv")
kv = open(kv_path, encoding="utf-8").read() if os.path.exists(kv_path) else ""
for rule in ("<LibraryScreen>:", "<ReaderScreen>:", "<SettingsScreen>:", "<BookRow>:"):
    need(rule in kv, f"chunkreader.kv is missing the {rule} section")

for font in ("OpenDyslexic-Regular.otf", "OpenDyslexic-Bold.otf"):
    need(os.path.exists(os.path.join(ROOT, "fonts", font)), f"fonts/{font} is missing")
need(os.path.exists(os.path.join(ROOT, "assets", "icon.png")), "assets/icon.png is missing")

spec_path = os.path.join(ROOT, "buildozer.spec")
need(os.path.exists(spec_path), "buildozer.spec is missing from the top folder")
if os.path.exists(spec_path):
    spec = configparser.ConfigParser()
    spec.read(spec_path)
    requirements = [r.strip().split("==")[0]
                    for r in spec.get("app", "requirements", fallback="").split(",")]
    need("pypdf" in requirements, "buildozer.spec: 'pypdf' is not in requirements")
    for stale in ("ebooklib", "beautifulsoup4", "pdfminer.six", "lxml"):
        need(stale not in requirements,
             f"buildozer.spec still lists '{stale}' - this is an old copy of the file")

need(not os.path.exists(os.path.join(ROOT, ".github", "workflows", "buildozer.spec")),
     "buildozer.spec was also saved in .github/workflows/ - delete that copy")

# The text engine has no Android or Kivy dependency, so test it for real.
if not problems:
    sys.path.insert(0, ROOT)
    import reader_engine
    sentences = reader_engine.split_sentences(
        'Dr. Smith walked in. "Is everyone ready?" he asked. The class fell silent.')
    need(len(sentences) == 3, f"sentence splitting is broken: {sentences}")
    units = reader_engine.paragraphs_to_units(
        [{"type": "heading", "text": "One"}, {"type": "para", "text": "A b. C d. E f. G h."}])
    chunks = reader_engine.build_chunks(units, 3)
    need([c["type"] for c in chunks] == ["heading", "text", "text"],
         f"chunk building is broken: {chunks}")

if problems:
    print("SELF-CHECK FAILED - the app was NOT built:\n")
    for problem in problems:
        print("  - " + problem)
    sys.exit(1)
print("Self-check passed: all files are complete and in the right place.")
