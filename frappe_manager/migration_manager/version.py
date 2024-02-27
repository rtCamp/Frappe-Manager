from dataclasses import dataclass
from functools import total_ordering

@total_ordering
@dataclass
class Version:
    version: str

    def __post_init__(self):
        self.version_parts = list(map(int, self.version.split('.')))

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.version_parts < other.version_parts

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.version_parts == other.version_parts

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.version_parts > other.version_parts

    def __str__(self):
        return self.version

    def version_string(self):
        return f"v{self.version}"
