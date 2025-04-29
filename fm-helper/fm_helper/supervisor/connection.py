import os
import time
import socket
from pathlib import Path
from xmlrpc.client import ServerProxy, Fault, ProtocolError
import supervisor.xmlrpc as sxml
from .exceptions import SupervisorConnectionError

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

def check_supervisord_connection(service_name: str) -> ServerProxy:
    """Checks connection and returns proxy, raising SupervisorConnectionError on failure."""
    conn = get_xml_connection(service_name) # Can raise SupervisorConnectionError if socket missing/not socket

    # Handle case where get_xml_connection returns None (socket didn't exist)
    if conn is None:
         raise SupervisorConnectionError(f"Socket file not found or invalid for service '{service_name}'", service_name=service_name)

    try:
        # Try a simple API call to verify responsiveness
        conn.supervisor.getState()
        return conn
    except Fault as e:
        raise SupervisorConnectionError(f"XML-RPC Fault during connection check: {e.faultString}", service_name=service_name, original_exception=e)
    except ProtocolError as e:
        raise SupervisorConnectionError(f"Protocol error during connection check: {e}", service_name=service_name, original_exception=e)
    except (OSError, IOError, ConnectionRefusedError, socket.error, socket.timeout) as e: # Added socket.timeout
        raise SupervisorConnectionError(f"Socket/Connection error during connection check: {e}", service_name=service_name, original_exception=e)
    except Exception as e:
        raise SupervisorConnectionError(f"Unexpected error during connection check: {e}", service_name=service_name, original_exception=e)
