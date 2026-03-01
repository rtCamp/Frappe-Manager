#!/bin/bash
# Wrapper script to execute arbitrary commands after entrypoint initialization
# Used by fm shell to run commands with proper UID remapping and environment setup
exec "$@"
