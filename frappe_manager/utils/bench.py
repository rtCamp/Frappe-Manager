from pathlib import Path
from typing import Dict, List

def get_all_benches(root_path: Path, exclude: List[str] = []) -> Dict[str, Path]:
    """Get all bench directories that contain a docker-compose.yml file"""
    benches = {}
    for dir in root_path.iterdir():
        if dir.is_dir() and dir.parts[-1] not in exclude:
            name = dir.parts[-1]
            compose_file = dir / "docker-compose.yml"
            if compose_file.exists():
                benches[name] = dir
    return benches

