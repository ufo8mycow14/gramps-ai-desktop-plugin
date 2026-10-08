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
"""Check local client contracts, discovery reuse and a real stdio handshake."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import clients


# -------------------------------------------------------------------------
# ClientContracts
# -------------------------------------------------------------------------
class ClientContracts(unittest.TestCase):
    """Exercise exports without client credentials, Gramps or a family tree."""

    def paths(self, root: Path) -> tuple[Path, Path, Path]:
        """Return real interpreter/server paths and a disposable discovery path."""
        return (
            Path(sys.executable),
            Path(clients.__file__).with_name("server.py"),
            root / "discovery",
        )

    def test_native_json_shapes(self) -> None:
        """Native maps retain exact executable arguments with spaces and Unicode."""
        with tempfile.TemporaryDirectory(prefix="Gramps O'Brien é ") as temporary:
            paths = self.paths(Path(temporary))
            for client in clients.CLIENTS:
                with self.subTest(client=client):
                    config = clients.client_config(client, *paths)
                    if client in ("opencode", "grok"):
                        entry = config["mcp"]["gramps_desktop"]
                        self.assertEqual(entry["type"], "local")
                        self.assertTrue(entry["enabled"])
                        self.assertEqual(
                            entry["command"],
                            [
                                str(paths[0]),
                                str(paths[1]),
                                "--runtime-dir",
                                str(paths[2]),
                            ],
                        )
                    else:
                        group = "mcp_servers" if client == "hermes" else "mcpServers"
                        entry = config[group]["gramps_desktop"]
                        self.assertEqual(entry["command"], str(paths[0]))
                        self.assertEqual(
                            entry["args"],
                            [str(paths[1]), "--runtime-dir", str(paths[2])],
                        )
                        self.assertEqual(
                            entry.get("type"),
                            "stdio" if client == "claude-code" else None,
                        )
                    self.assertNotIn("model", config)
                    self.assertNotIn("env", entry)
                    self.assertNotIn("cwd", entry)

    def test_exported_transport_negotiates_all_tools_from_another_cwd(self) -> None:
        """Launch every generated transport outside the checkout and list 58 tools."""
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "gramps_health", "arguments": {}},
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for client in clients.CLIENTS:
                with self.subTest(client=client):
                    config = clients.client_config(client, *self.paths(root))
                    if client in ("opencode", "grok"):
                        command = config["mcp"]["gramps_desktop"]["command"]
                    else:
                        group = "mcp_servers" if client == "hermes" else "mcpServers"
                        entry = config[group]["gramps_desktop"]
                        command = [entry["command"], *entry["args"]]
                    result = subprocess.run(
                        command,
                        input="\n".join(json.dumps(item) for item in requests) + "\n",
                        cwd=root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                        check=True,
                        env=dict(
                            os.environ, GRAMPS_DESKTOP_RUNTIME="inherited-relative-path"
                        ),
                    )
                    replies = [json.loads(line) for line in result.stdout.splitlines()]
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(
                        replies[0]["result"]["serverInfo"]["name"],
                        "gramps-ai-desktop-plugin",
                    )
                    self.assertEqual(len(replies[1]["result"]["tools"]), 58)
                    diagnostic = json.loads(replies[2]["result"]["content"][0]["text"])[
                        "result"
                    ]
                    self.assertEqual(
                        diagnostic["runtime_path"],
                        str(root / "discovery" / "connection.json"),
                    )
                    self.assertEqual(diagnostic["reason"], "discovery_missing")
            self.assertFalse((root / "discovery").exists())

    def test_invalid_default_environment_reports_a_clear_error(self) -> None:
        """Reject a relative environment path when no explicit override is supplied."""
        result = subprocess.run(
            [sys.executable, str(Path(clients.__file__).with_name("server.py"))],
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ, GRAMPS_DESKTOP_RUNTIME="relative"),
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "GRAMPS_DESKTOP_RUNTIME must be an absolute directory", result.stderr
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_hermes_yaml_quotes_windows_paths(self) -> None:
        """YAML scalar and array values decode without losing backslashes."""
        config = {
            "mcp_servers": {
                "gramps_desktop": {
                    "command": "C:\\O'Brien é\\python.exe",
                    "args": [
                        "C:\\path with spaces\\server.py",
                        "--runtime-dir",
                        "C:\\runtime",
                    ],
                }
            }
        }
        lines = clients.render_config("hermes", config).splitlines()
        entry = config["mcp_servers"]["gramps_desktop"]
        self.assertEqual(json.loads(lines[2].split(": ", 1)[1]), entry["command"])
        self.assertEqual(json.loads(lines[3].split(": ", 1)[1]), entry["args"])

    def test_registration_contracts(self) -> None:
        """Kimi has no scope, Hermes keeps remainder last and OpenCode is interactive."""
        with tempfile.TemporaryDirectory() as temporary:
            paths = self.paths(Path(temporary))
            claude = clients.registration_args("claude-code", *paths)
            self.assertEqual(
                claude[:8],
                [
                    "claude",
                    "mcp",
                    "add",
                    "--transport",
                    "stdio",
                    "--scope",
                    "user",
                    "gramps_desktop",
                ],
            )
            kimi = clients.registration_args("kimi", *paths)
            self.assertNotIn("--scope", kimi)
            self.assertEqual(kimi[kimi.index("--") + 1 :], clients.launch_args(*paths))
            hermes = clients.registration_args("hermes", *paths)
            self.assertEqual(
                hermes[hermes.index("--args") + 1 :], clients.launch_args(*paths)[1:]
            )
            for client in ("opencode", "grok"):
                self.assertEqual(
                    clients.registration_args(client, *paths),
                    ["opencode", "mcp", "add"],
                )

    def test_shell_quotes_keep_registration_tokens_literal(self) -> None:
        """Commands preserve spaces, quotes and shell metacharacters as arguments."""
        tokens = ["cli", "O'Brien space", "$(example); `value`", "é"]
        self.assertEqual(shlex.split(clients.render_command(tokens, "posix")), tokens)
        powershell = clients.render_command(tokens, "powershell")
        self.assertIn("'O''Brien space'", powershell)
        self.assertIn("'$(example); `value`'", powershell)
        with self.assertRaises(ValueError):
            clients.render_command(tokens, "cmd")

    def test_discovery_reuses_locator_without_reading_connection(self) -> None:
        """An installed explicit path takes precedence over a different host default."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "shared"
            (root / "bridge_source.json").write_text(
                json.dumps({"runtime": str(runtime)})
            )
            (root / "connection.json").write_text("must never be read")
            with patch.object(
                clients, "runtime_dir", side_effect=AssertionError("wrong host default")
            ):
                self.assertEqual(clients.shared_runtime(None, root), runtime)
                self.assertEqual(
                    clients.shared_runtime(root / "override", root), root / "override"
                )

    def test_discovery_default_and_invalid_locator(self) -> None:
        """Resolve a fresh host default and reject ambiguous stored paths."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(clients, "runtime_dir", return_value=root / "default"):
                self.assertEqual(clients.shared_runtime(None, root), root / "default")
            for runtime in ("relative", "", 17):
                with self.subTest(runtime=runtime):
                    (root / "bridge_source.json").write_text(
                        json.dumps({"runtime": runtime})
                    )
                    with self.assertRaises(ValueError):
                        clients.shared_runtime(None, root)

    def test_invalid_client_and_nonabsolute_or_missing_paths(self) -> None:
        """Reject commands that could silently select another local installation."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.paths(root)
            with self.assertRaises(ValueError):
                clients.client_config("unknown", *paths)
            for index in range(3):
                invalid = list(paths)
                invalid[index] = Path("relative")
                with self.assertRaises(ValueError):
                    clients.client_config("kimi", invalid[0], invalid[1], invalid[2])
            with self.assertRaises(ValueError):
                clients.client_config("kimi", root / "missing", paths[1], paths[2])

    def test_output_preserves_existing_file_by_default(self) -> None:
        """No export overwrites an existing client configuration implicitly."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "config.json"
            clients.write_output(output, "original\n")
            with self.assertRaises(FileExistsError):
                clients.write_output(output, "new\n")
            self.assertEqual(output.read_text(), "original\n")
            clients.write_output(output, "reviewed\n", overwrite=True)
            self.assertEqual(output.read_text(), "reviewed\n")


if __name__ == "__main__":
    unittest.main()
