[app]
title = ChunkReader
package.name = chunkreader
package.domain = org.sandernoordijk

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,otf,ttf,json,txt
source.exclude_dirs = bin, .github, .buildozer, venv, .venv

# The version number lives in one place: __version__ at the top of main.py.
# It is shown at the bottom of the Settings screen, together with the
# GitHub build number, so you can always see which build is installed.
version.regex = __version__ = ['"](.*)['"]
version.filename = %(source.dir)s/main.py

# Deliberately minimal. EPUB parsing uses only the Python standard
# library; pypdf (pure Python, no dependencies of its own) reads PDFs.
# pyjnius and android come with the Kivy recipe and need no entry here.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,pypdf

orientation = portrait,landscape
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png

# No storage permissions: books are chosen with the system file picker,
# which needs none.

android.api = 34
# 26 (Android 8.0) is the lowest level this Python build compiles for.
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
