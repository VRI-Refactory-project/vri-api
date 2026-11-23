"""Documents sent in a chat: check what the file really is, and keep it.

Only a short list of everyday document types is accepted, and each one is checked against its own
first bytes, not just its name, so a program renamed to "notes.pdf" is refused. Like photos, files are
kept in the database (see MessageFile) because the free Render disk is wiped on every deploy.
"""

import os
import re

# The first bytes of each kind of file. Word, Excel and PowerPoint's newer formats are zip archives;
# the older ones share the "OLE" header.
PDF = b"%PDF-"
ZIP = b"PK\x03\x04"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# extension -> (content type served back, how to recognise it)
ALLOWED = {
    "pdf": ("application/pdf", PDF),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ZIP,
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ZIP,
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ZIP,
    ),
    "doc": ("application/msword", OLE),
    "xls": ("application/vnd.ms-excel", OLE),
    "ppt": ("application/vnd.ms-powerpoint", OLE),
    "txt": ("text/plain; charset=utf-8", None),
    "csv": ("text/csv; charset=utf-8", None),
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_NAME = 100
ACCEPTED_LABEL = "PDF, Word, Excel, PowerPoint, TXT or CSV"


class InvalidFile(Exception):
    """The upload is not allowed. The message is safe to show the user."""


def clean_name(name):
    """A safe display name: no folders, no control characters, at most MAX_NAME long."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if len(name) > MAX_NAME:
        stem, dot, extension = name.rpartition(".")
        name = (
            stem[: MAX_NAME - len(extension) - 1] + dot + extension
            if dot
            else name[:MAX_NAME]
        )
    return name


def _is_plain_text(head):
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # The sample may stop in the middle of a character; that is not a problem.
        if error.start < len(head) - 3:
            return False
    return True


def process_chat_file(uploaded_file):
    """Return (bytes, clean name, content type) for an upload, or raise InvalidFile."""
    name = clean_name(uploaded_file.name)
    extension = name.rpartition(".")[2].lower() if "." in name else ""
    if extension not in ALLOWED:
        raise InvalidFile(f"That kind of file is not allowed. Use {ACCEPTED_LABEL}.")
    if uploaded_file.size == 0:
        raise InvalidFile("That file is empty.")
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise InvalidFile("That file is too large. Choose one under 10 MB.")

    content_type, signature = ALLOWED[extension]
    data = uploaded_file.read()
    if signature is None:
        if not _is_plain_text(data[:4096]):
            raise InvalidFile("That does not look like a text file.")
    elif not data.startswith(signature):
        raise InvalidFile(f"That file is not a real .{extension} file.")
    return data, name, content_type
