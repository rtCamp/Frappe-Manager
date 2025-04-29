import os
from pathlib import Path
from xmlrpc.client import ServerProxy
import supervisor.xmlrpc as sxml

# Re-export the socket directory constant
FM_SUPERVISOR_SOCKETS_DIR = Path(
    os.environ.get("SUPERVISOR_SOCKET_DIR", "/fm-sockets")
)

def get_xml_connection(service_name: str) -> ServerProxy:
    """Get an XML-RPC connection to the supervisord instance for the given service."""
    socket_path = FM_SUPERVISOR_SOCKETS_DIR / f"{service_name}.sock"
    if not socket_path.exists():
        return None
        
    return ServerProxy(
        "http://127.0.0.1",  # Placeholder URL, not used for UNIX sockets
        transport=sxml.SupervisorTransport(
            serverurl=f"unix://{socket_path.resolve()}"
        ),
    )
