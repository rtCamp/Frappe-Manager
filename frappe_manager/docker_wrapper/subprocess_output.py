from dataclasses import dataclass
from typing import List


@dataclass
class SubprocessOutput:
    stdout: List[str]
    stderr: List[str]
    combined: List[str]
    exit_code: int

    @classmethod
    def from_output(cls, output):
        stdout = []
        stderr = []
        combined = []
        exit_code = 0

        for source, line in output:
            line = line.decode()
            if source == 'exit_code':
                exit_code = int(line)
            else:
                combined.append(line)
            if source == 'stdout':
                stdout.append(line)
            if source == 'stderr':
                stderr.append(line)

        data = {'stdout': stdout, 'stderr': stderr, 'combined': combined, 'exit_code': exit_code}
        return cls(**data)
