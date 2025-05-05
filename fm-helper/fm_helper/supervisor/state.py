import json
from pathlib import Path
from typing import Optional, Tuple, Dict

from .constants import FM_SUPERVISOR_STATE_DIR, DEFAULT_SUFFIXES

class RollingState:
    """Manages state for Blue/Green worker deployments."""
    
    def __init__(self, suffixes: str = DEFAULT_SUFFIXES):
        self.suffix_pairs = [s.strip() for s in suffixes.split(',')]
        if len(self.suffix_pairs) != 2:
            raise ValueError(f"Expected exactly 2 suffixes, got: {suffixes}")
        
        # Ensure state directory exists with proper permissions
        FM_SUPERVISOR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        FM_SUPERVISOR_STATE_DIR.chmod(0o755)  # World readable

    def _get_state_file(self, worker_group: str) -> Path:
        """Get path to state file for a worker group."""
        return FM_SUPERVISOR_STATE_DIR / f"{worker_group}.active_suffix"

    def get_active_suffix(self, worker_group: str) -> str:
        """Get active suffix for a worker group. Returns first suffix if no state exists."""
        state_file = self._get_state_file(worker_group)
        try:
            if state_file.exists():
                active = state_file.read_text().strip()
                if active in self.suffix_pairs:
                    return active
        except (IOError, OSError) as e:
            print(f"[yellow]Warning:[/yellow] Could not read state file for {worker_group}: {e}")
        return self.suffix_pairs[0]  # Default to first suffix

    def get_inactive_suffix(self, worker_group: str) -> str:
        """Get inactive suffix based on current active one."""
        active = self.get_active_suffix(worker_group)
        return self.suffix_pairs[1] if active == self.suffix_pairs[0] else self.suffix_pairs[0]

    def set_active_suffix(self, worker_group: str, suffix: str) -> bool:
        """Update active suffix for a worker group."""
        if suffix not in self.suffix_pairs:
            raise ValueError(f"Invalid suffix '{suffix}'. Must be one of: {self.suffix_pairs}")
        
        state_file = self._get_state_file(worker_group)
        try:
            state_file.write_text(suffix)
            return True
        except (IOError, OSError) as e:
            print(f"[red]Error:[/red] Could not write state file for {worker_group}: {e}")
            return False

    def get_suffixes(self) -> Tuple[str, str]:
        """Return the configured suffix pair."""
        return tuple(self.suffix_pairs)

    def get_process_names(self, base_name: str) -> Dict[str, str]:
        """Get full process names with both suffixes for a base worker name."""
        return {
            suffix: f"{base_name}-{suffix}"
            for suffix in self.suffix_pairs
        }
