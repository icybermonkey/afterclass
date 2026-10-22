"""What every input source shares: the output folder layout and step caching."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Ctx:
    input: Path
    out: Path
    args: object
    cfg: dict
    warnings: list = field(default_factory=list)

    def __post_init__(self):
        self.media = self.out / "media"
        self.slides = self.out / "slides"
        self.deck = self.out / "deck"
        self.data = self.out / "data"
        for d in (self.out, self.data):
            d.mkdir(parents=True, exist_ok=True)
        self.redo = set(self.args.redo or [])

    def cached(self, step, *paths):
        """True when the step's results exist and no re-run was asked for."""
        if self.args.force or step in self.redo:
            return False
        return all(Path(p).exists() for p in paths)


class Source:
    name = ""
    extensions = ()
    planned = False  # registered but not implemented yet

    def accepts(self, path):
        return path.suffix.lower() in self.extensions

    def run(self, ctx):
        """Run all steps and return the path of index.html."""
        raise NotImplementedError
