#!/usr/bin/env python3

import signal
import sys
import time

import ngrok

from frappe_manager.exceptions import FrappeManagerException
from frappe_manager.output_manager import get_global_output_handler, set_global_output_handler, spinner
from frappe_manager.output_manager.rich_output import RichOutputHandler


class NgrokTunnelError(FrappeManagerException):
    """The tunnel could not be established. Carries a message with the token removed."""


def _scrub(text: str, auth_token: str) -> str:
    """ngrok returns its errors as a raw tuple whose text repeats the token back. Flatten it to
    prose and remove the one value we know is a secret."""
    cleaned = text.strip("()").replace("\\n", "\n")
    return cleaned.replace(auth_token, "***") if auth_token else cleaned


def create_tunnel(site_name: str, auth_token: str, port: int = 80) -> None:
    """
    Create an ngrok HTTP tunnel for the specified site name and keep it running.

    Args:
        site_name: The site name to use for host header
        auth_token: Ngrok authentication token
        port: The local port to tunnel to (default: 80)
    """
    try:
        output = get_global_output_handler()
    except RuntimeError:
        output = RichOutputHandler()
        set_global_output_handler(output)

    with spinner(output, f"Forwarding all requests from {site_name}"):
        try:
            ngrok.set_auth_token(auth_token)

            listener = ngrok.forward(
                port=port,
                authtoken=auth_token,
                request_header_add=[f"Host: {site_name}"],
                opts={"addr": str(port), "host_header": site_name},
            )

            tunnel_url = listener.url()
        except Exception as e:
            # ngrok's own error text ECHOES the token back ("Your authtoken: <value>"), so it
            # cannot be surfaced as it arrives: it put the credential in fm.log three times over.
            # Redaction that keys on option names never sees this -- the secret sits inside prose
            # fm did not compose -- so the known value is replaced literally.
            #
            # `from None`, not `from e`: chaining keeps the original exception alive, and the
            # traceback fm logs renders ITS message, which is how the token still reached fm.log
            # after the printed message was already clean. The scrubbed text is the whole content.
            raise NgrokTunnelError(_scrub(str(e), auth_token)) from None

    output.print(f"Ingress established at: {tunnel_url}")

    def signal_handler(sig, frame):
        output.print("Shutting down ngrok tunnel...")
        listener.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.close()
        sys.exit(0)
    except Exception as e:
        # Same echo risk as the setup path: an ngrok-side failure mid-tunnel can repeat the token.
        output.display_error(f"Error in tunnel: {_scrub(str(e), auth_token)}")
        listener.close()
        sys.exit(1)

