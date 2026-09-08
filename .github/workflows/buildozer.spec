[app]
title = ChunkReader
package.name = chunkreader
package.domain = org.sandernoordijk

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,otf,ttf,json,txt

version = 0.1.0

# Pinned to 4.12.3: BeautifulSoup 4.13+ added a hard dependency on
# typing_extensions (bs4/_typing.py), and python-for-android's packaging
# step doesn't reliably bundle that single-file module into the APK,
# causing "ModuleNotFoundError: No module named 'typing_extensions'" at
# launch. 4.12.x has no such dependency, so this sidesteps the problem
# entirely rather than fighting the packaging step.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,plyer,ebooklib,beautifulsoup4==4.12.3,pdfminer.six

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
