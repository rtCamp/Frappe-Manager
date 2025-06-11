import os
import time
import socket
from pathlib import Path
from xmlrpc.client import ServerProxy, Fault, ProtocolError
import supervisor.xmlrpc as sxml
from .exceptions import SupervisorConnectionError

FM_SUPERVISOR_SOCKETS_DIR = Path(
    os.environ.get("SUPERVISOR_SOCKET_DIR", "/fm-sockets")
)

def get_xml_connection(service_name: str) -> ServerProxy:
    """
    Creates an XML-RPC connection to a supervisord instance via Unix socket.
    
    Logic:
    1. Constructs socket path using service name and configured socket directory
    2. Checks if the socket file physically exists on filesystem
    3. If socket missing: returns None (caller handles this case)
    4. If socket exists: creates ServerProxy with supervisor transport layer
    5. Uses Unix domain socket transport instead of TCP for local communication
    6. Returns configured proxy object ready for API calls
    
    Returns:
        ServerProxy object for making supervisor API calls, or None if socket missing
    """
    socket_path = FM_SUPERVISOR_SOCKETS_DIR / f"{service_name}.sock"
    if not socket_path.exists():
        return None
        
    return ServerProxy(
        "http://127.0.0.1",
        transport=sxml.SupervisorTransport(
            serverurl=f"unix://{socket_path.resolve()}"
        ),
    )

def check_supervisord_connection(service_name: str) -> ServerProxy:
    """
    Validates supervisor connection by testing actual API responsiveness.
    
    Logic:
    1. Gets XML-RPC connection using get_xml_connection
    2. If connection is None: raises SupervisorConnectionError (socket missing)
    3. Tests connection by calling supervisor.getState() API method
    4. If API call succeeds: supervisor is responsive, returns proxy
    5. If API call fails: catches specific error types and converts to SupervisorConnectionError
    6. Handles XML-RPC faults, protocol errors, socket errors, and timeouts
    7. Provides detailed error context including original exception
    
    Returns:
        Validated ServerProxy object ready for supervisor operations
        
    Raises:
        SupervisorConnectionError: For any connection or responsiveness issues
    """
    conn = get_xml_connection(service_name)
    if conn is None:
         raise SupervisorConnectionError(f"Socket file not found or invalid for service '{service_name}'", service_name=service_name)

    try:
        conn.supervisor.getState()
        return conn
    except Fault as e:
        raise SupervisorConnectionError(f"XML-RPC Fault during connection check: {e.faultString}", service_name=service_name, original_exception=e)
    except ProtocolError as e:
        raise SupervisorConnectionError(f"Protocol error during connection check: {e}", service_name=service_name, original_exception=e)
    except (OSError, IOError, ConnectionRefusedError, socket.error, socket.timeout) as e:
        raise SupervisorConnectionError(f"Socket/Connection error during connection check: {e}", service_name=service_name, original_exception=e)
    except Exception as e:
        raise SupervisorConnectionError(f"Unexpected error during connection check: {e}", service_name=service_name, original_exception=e)
