"""
android_picker.py

Android-only: lets the user pick a book with the system file picker and
copies it into the app's own storage.

This uses Android's Storage Access Framework, which needs no storage
permission at all and works on every Android version this app supports.
(The old approach - asking for READ_EXTERNAL_STORAGE and opening a file
path - no longer works on modern Android, where that permission is
simply ignored.)

This module is only ever imported on Android, and every call into it is
wrapped by main.py, so a problem here shows an error message instead of
closing the app.
"""

import os
import shutil

from jnius import autoclass

from android import activity

try:
    from android import mActivity
except ImportError:  # older python-for-android
    mActivity = autoclass("org.kivy.android.PythonActivity").mActivity

REQUEST_CODE = 4711
RESULT_OK = -1

_pending_callback = None


def pick_document(on_result):
    """
    Open the system file picker.

    on_result(uri_string or None) is called when the user has chosen a
    file or cancelled. NOTE: it is called on Android's UI thread, so the
    caller must hand over to the Kivy thread (Clock.schedule_once).
    """
    global _pending_callback
    Intent = autoclass("android.content.Intent")

    intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    # All files are shown; the app checks afterwards that the chosen file
    # really is an EPUB or PDF. (Some file providers label .epub files with
    # a generic type, so filtering here could grey out valid books.)
    intent.setType("*/*")

    _pending_callback = on_result
    activity.unbind(on_activity_result=_on_activity_result)
    activity.bind(on_activity_result=_on_activity_result)
    mActivity.startActivityForResult(intent, REQUEST_CODE)


def _on_activity_result(request_code, result_code, intent):
    global _pending_callback
    if request_code != REQUEST_CODE:
        return
    callback, _pending_callback = _pending_callback, None
    if callback is None:
        return

    uri_string = None
    try:
        if result_code == RESULT_OK and intent is not None:
            uri = intent.getData()
            if uri is not None:
                uri_string = uri.toString()
    finally:
        callback(uri_string)


def _parse_uri(uri_string):
    return autoclass("android.net.Uri").parse(uri_string)


def query_display_name(uri_string):
    """The file's name as shown in the picker (e.g. 'My Book.epub'), or None."""
    cursor = None
    try:
        resolver = mActivity.getContentResolver()
        cursor = resolver.query(_parse_uri(uri_string), None, None, None, None)
        if cursor is not None and cursor.moveToFirst():
            column = cursor.getColumnIndex("_display_name")
            if column >= 0:
                return cursor.getString(column)
    except Exception:
        return None
    finally:
        if cursor is not None:
            cursor.close()
    return None


def copy_uri_to_path(uri_string, dest_path):
    """Copy the picked document into a normal file the app can read."""
    resolver = mActivity.getContentResolver()
    uri = _parse_uri(uri_string)

    # Preferred: get a file descriptor and let Python do the copying.
    try:
        descriptor = resolver.openFileDescriptor(uri, "r")
        try:
            fd = os.dup(descriptor.getFd())
        finally:
            descriptor.close()
        with os.fdopen(fd, "rb") as source, open(dest_path, "wb") as dest:
            shutil.copyfileobj(source, dest, 1024 * 1024)
        if os.path.getsize(dest_path) > 0:
            return
    except Exception:
        pass

    # Fallback: let Java copy the stream (Android 10 and newer).
    FileUtils = autoclass("android.os.FileUtils")
    FileOutputStream = autoclass("java.io.FileOutputStream")
    source = resolver.openInputStream(uri)
    dest = FileOutputStream(dest_path)
    try:
        FileUtils.copy(source, dest)
        dest.flush()
    finally:
        dest.close()
        source.close()
