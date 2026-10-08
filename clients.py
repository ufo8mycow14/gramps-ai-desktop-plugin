#
# Gramps - a GTK+/GNOME based genealogy program
#
# Copyright (C) 2026  Gramps Codex Desktop Plugin contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
"""Generate local client configuration without touching credentials or settings."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from typing import Any

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
from platform_paths import addon_dir, runtime_dir, SUPPORTED_GRAMPS

CLIENTS = ("claude-code", "kimi", "hermes", "opencode", "grok")
SERVER_KEY = "gramps_desktop"


def shared_runtime(runtime: Path | None, addon: Path) -> Path:
    """Reuse the installed bridge's explicit discovery path where available."""
    if runtime is None:
        locator = addon / "bridge_source.json"
        if locator.is_file():
            settings = json.loads(locator.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("The installed bridge locator must be an object")
            value = settings.get("runtime")
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError("The installed bridge runtime must be a path")
                runtime = Path(value)
        if runtime is None:
            runtime = runtime_dir()
    if not runtime.is_absolute():
        raise ValueError("The bridge runtime directory must be absolute")
    return runtime.resolve()


def launch_args(python: Path, server: Path, runtime: Path) -> list[str]:
    """Build an absolute, working-directory-independent stdio command."""
    for name, path in (("Python", python), ("server", server), ("runtime", runtime)):
        if not path.is_absolute():
            raise ValueError(name + " path must be absolute")
    if not python.is_file() or not server.is_file():
        raise ValueError("Python and server paths must name existing files")
    return [str(python), str(server), "--runtime-dir", str(runtime)]


def client_config(
    client: str, python: Path, server: Path, runtime: Path
) -> dict[str, Any]:
    """Return the documented native configuration shape for one client."""
    if client not in CLIENTS:
        raise ValueError("Unknown client: " + client)
    command = launch_args(python, server, runtime)
    if client in ("opencode", "grok"):
        return {
            "$schema": "https://opencode.ai/config.json",
            "mcp": {SERVER_KEY: {"type": "local", "command": command, "enabled": True}},
        }
    entry: dict[str, Any] = {"command": command[0], "args": command[1:]}
    if client == "claude-code":
        entry = {"type": "stdio", **entry}
    if client == "hermes":
        return {"mcp_servers": {SERVER_KEY: entry}}
    return {"mcpServers": {SERVER_KEY: entry}}


def render_config(client: str, config: dict[str, Any]) -> str:
    """Render Hermes YAML or another client's JSON without extra dependencies."""
    if client == "hermes":
        entry = config["mcp_servers"][SERVER_KEY]
        return (
            "mcp_servers:\n  "
            + SERVER_KEY
            + ":\n"
            + "    command: "
            + json.dumps(entry["command"], ensure_ascii=False)
            + "\n"
            + "    args: "
            + json.dumps(entry["args"], ensure_ascii=False)
            + "\n"
        )
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def registration_args(
    client: str, python: Path, server: Path, runtime: Path
) -> list[str]:
    """Return registration tokens; OpenCode uses its interactive add command."""
    command = launch_args(python, server, runtime)
    if client == "claude-code":
        return [
            "claude",
            "mcp",
            "add",
            "--transport",
            "stdio",
            "--scope",
            "user",
            SERVER_KEY,
            "--",
            *command,
        ]
    if client == "kimi":
        return [
            "kimi",
            "mcp",
            "add",
            "--transport",
            "stdio",
            SERVER_KEY,
            "--",
            *command,
        ]
    if client == "hermes":
        return [
            "hermes",
            "mcp",
            "add",
            SERVER_KEY,
            "--command",
            command[0],
            "--args",
            *command[1:],
        ]
    if client in ("opencode", "grok"):
        return ["opencode", "mcp", "add"]
    raise ValueError("Unknown client: " + client)


def render_command(command: list[str], shell: str) -> str:
    """Quote registration tokens for the chosen local shell."""
    if shell == "powershell":
        return "& " + " ".join(
            "'" + token.replace("'", "''") + "'" for token in command
        )
    if shell == "posix":
        return shlex.join(command)
    raise ValueError("Choose powershell or posix command output")


def write_output(path: Path, content: str, overwrite: bool = False) -> None:
    """Write an explicitly requested snippet; preserve existing files by default."""
    with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main() -> None:
    """Export a configuration snippet or registration command for one client."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=CLIENTS, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--server", type=Path, default=Path(__file__).with_name("server.py")
    )
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--addon-dir", type=Path)
    parser.add_argument("--gramps-version", choices=SUPPORTED_GRAMPS, default="6.1")
    parser.add_argument("--format", choices=("config", "command"), default="config")
    parser.add_argument(
        "--shell",
        choices=("powershell", "posix"),
        default="powershell" if os.name == "nt" else "posix",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.overwrite and args.output is None:
        parser.error("--overwrite requires an explicit --output file")
    try:
        runtime = shared_runtime(
            args.runtime_dir, args.addon_dir or addon_dir(args.gramps_version)
        )
        if args.format == "command":
            content = (
                render_command(
                    registration_args(args.client, args.python, args.server, runtime),
                    args.shell,
                )
                + "\n"
            )
        else:
            content = render_config(
                args.client,
                client_config(args.client, args.python, args.server, runtime),
            )
        if args.output is None:
            sys.stdout.write(content)
        else:
            write_output(args.output, content, args.overwrite)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
