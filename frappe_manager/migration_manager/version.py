from dataclasses import dataclass
from functools import total_ordering
from packaging.version import Version as PackagingVersion


@total_ordering
@dataclass
class Version:
    version: str

    def __post_init__(self):
        self._parsed = PackagingVersion(self.version)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._parsed < other._parsed

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._parsed == other._parsed

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._parsed > other._parsed

    def __str__(self):
        return self.version

    def version_string(self):
        return f"v{self.version}"
