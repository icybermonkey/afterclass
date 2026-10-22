"""Pick the source for an input file. New input types = one file in sources/ + one line here."""

from ..ui import AfterclassError
from .base import Source
from .video import VideoSource


class Planned(Source):
    planned = True

    def __init__(self, name, extensions, hint):
        self.name, self.extensions, self.hint = name, extensions, hint


SOURCES = [
    VideoSource(),
    Planned("audio", (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus"),
            "audio-only lessons are planned; for now use the video recording"),
    Planned("pdf", (".pdf",), "a PDF alone is planned; for now pass it with --deck next to the video"),
    Planned("pptx", (".pptx", ".ppt", ".key"), "slides alone are planned; for now pass them with --deck next to the video"),
]


def pick(path):
    for src in SOURCES:
        if src.accepts(path):
            if src.planned:
                raise AfterclassError(f"{src.name.upper()} input is not supported yet (planned): {src.hint}")
            return src
    raise AfterclassError(f"don't know how to read {path.suffix or path.name} files. Supported: video "
                     f"({', '.join(VideoSource.extensions)})")
