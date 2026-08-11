[app]
title = ChunkReader
package.name = chunkreader
package.domain = org.sandernoordijk

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,otf,ttf,json,txt

version = 0.1.0

requirements = python3,kivy==2.3.0,plyer,ebooklib,beautifulsoup4,pdfminer.six,lxml

# Portrait only - matches the "textbox" reading metaphor
orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png

android.permissions = READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE

android.api = 34
# 24 (Android 7.0), not 23: the bundled CPython build needs preadv/pwritev,
# which bionic libc only provides from API 24 onward. API 23 fails to
# compile with an "implicit declaration" error under -Werror.
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
