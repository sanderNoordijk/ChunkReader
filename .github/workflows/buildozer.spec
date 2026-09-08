[app]
title = ChunkReader
package.name = chunkreader
package.domain = org.sandernoordijk

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,otf,ttf,json,txt

version = 0.1.0

# Deliberately minimal: no ebooklib, no beautifulsoup4, no pdfminer.six.
# ebooklib requires lxml (a compiled C extension - fragile to
# cross-compile via python-for-android's non-recipe pip install path,
# and the direct cause of an earlier build failure). pdfminer.six
# requires charset-normalizer AND cryptography (the latter also a
# compiled extension). beautifulsoup4 4.13+ requires typing_extensions,
# which p4a's packaging step didn't reliably bundle. reader_engine.py
# was rewritten to use only the Python standard library (zipfile +
# xml.etree.ElementTree + html.parser) for EPUB parsing, and pypdf
# (zero required third-party dependencies for plain text extraction)
# for PDF - this requirements line reflects that.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,plyer,pypdf

# "all" rather than "portrait": forcing portrait caused an immediate
# forced rotation on tablets that launch in landscape, which triggered a
# native SIGABRT crash (HWUI render-thread mutex teardown race) on launch.
# The chunk-reader UI still centers/scales fine in landscape.
orientation = all
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

android.api = 34
# 26 (Android 8.0): bionic libc only gained preadv/pwritev at API 24 and
# the grp-database functions (setgrent/getgrent/endgrent) at API 26.
# CPython's build needs both, so 26 is the real floor for this toolchain.
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
