"""prawnik-mcp: MCP server for Polish and EU law with source provenance."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prawnik-mcp")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+local"
