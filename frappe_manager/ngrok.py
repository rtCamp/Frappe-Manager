#!/usr/bin/env python3

import ngrok
import asyncio
import signal
import sys
import time
from frappe_manager.display_manager.DisplayManager import richprint
from typing import Optional


def create_tunnel(site_name: str, auth_token: str, port: int = 80) -> None:
    """
    Create an ngrok HTTP tunnel for the specified site name and keep it running.

    Args:
        site_name: The site name to use for host header
        auth_token: Ngrok authentication token
        port: The local port to tunnel to (default: 80)
    """
    richprint.start(f"Forwarding all requests from {site_name}")

    try:
        # Configure ngrok with auth token
        ngrok.set_auth_token(auth_token)

        # Start ngrok HTTP tunnel
        listener = ngrok.forward(
            port=port,
            authtoken=auth_token,
            request_header_add=[f"Host: {site_name}"],
            opts={"addr": str(port), "host_header": site_name},
        )

        tunnel_url = listener.url()
        print(f"Ingress established at: {tunnel_url}")

        # Handle graceful shutdown
        def signal_handler(sig, frame):
            print("\nShutting down ngrok tunnel...")
            listener.close()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # Keep the tunnel open
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            listener.close()
            sys.exit(0)
        except Exception as e:
            print(f"Error in tunnel: {e}")
            listener.close()
            sys.exit(1)

    except Exception as e:
        print(f"Error creating tunnel: {e}")


async def start_tunnel(site_name: str):
    """
    Start an ngrok tunnel and keep it running until interrupted.

    Args:
        site_name: The site name to use for host header
    """
    listener = await ngrok.connect(
        80,
        authtoken=auth_token,
        request_header_add=[f"Host: {site_name}"],
        opts={"addr": "80", "host_header": site_name},
    )

    print(f"Ingress established at: {listener.url()}")

    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutting down ngrok tunnel...")
        asyncio.create_task(listener.close())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Keep the tunnel open
    try:
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print(f"Error: {e}")
        await listener.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: ngrok.py <site_name>")
        sys.exit(1)
    asyncio.run(start_tunnel(sys.argv[1]))
