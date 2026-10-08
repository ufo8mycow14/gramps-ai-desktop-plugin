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
"""Exercise installer recovery using temporary files and a mocked registry."""

# -------------------------------------------------------------------------
# Standard Python modules
# -------------------------------------------------------------------------
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

# -------------------------------------------------------------------------
# Gramps plugin modules
# -------------------------------------------------------------------------
import install


# -------------------------------------------------------------------------
# InstallerRecovery
# -------------------------------------------------------------------------
class InstallerRecovery(unittest.TestCase):
    """Check registry failure, compensated file writes and successful retry."""

    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        """Create a minimal disposable installer source and workspace."""
        source, project, addon = root / "source", root / "project", root / "addon"
        for folder in (
            source / ".agents/plugins",
            source / ".codex-plugin",
            project,
            addon,
        ):
            folder.mkdir(parents=True)
        (source / ".agents/plugins/marketplace.json").write_text(
            json.dumps({"name": "gramps-desktop-plugins"})
        )
        (source / ".codex-plugin/plugin.json").write_text(
            json.dumps({"name": "gramps-desktop", "version": "2.8.0"})
        )
        (source / "DesktopControl.py").write_text("# bridge_source.json\n")
        (source / "DesktopControl.gpr.py").write_text('gramps_target_version = "6.1"\n')
        (source / ".mcp.json").write_text("original transport\n")
        (addon / "DesktopControl.py").write_text(
            "# bridge_source.json\n# original loader\n"
        )
        return source, project, addon

    def test_registry_failure_restores_transport_before_loader_writes(self) -> None:
        """A failed registry call must leave loader and transport unchanged."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            before = {
                path: path.read_bytes()
                for path in (source / ".mcp.json", addon / "DesktopControl.py")
            }
            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(
                install.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, ["codex"]),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    install.install(project, addon=addon, codex="mock-codex")
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertFalse((addon / "bridge_source.json").exists())
            self.assertFalse((project / ".codex/config.toml").exists())

    def test_registration_caches_staged_transport(self) -> None:
        """Codex sees the supplied runtime and interpreter before caching files."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            runtime = Path(temporary) / "runtime"
            before = (addon / "DesktopControl.py").read_bytes()
            cached: list[dict] = []

            def capture(command: list[str], **kwargs: object) -> None:
                """Observe the source exactly when the CLI caches the plugin."""
                self.assertEqual((addon / "DesktopControl.py").read_bytes(), before)
                if command[1:3] == ["plugin", "add"]:
                    cached.append(json.loads((source / ".mcp.json").read_text()))

            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(install.subprocess, "run", side_effect=capture):
                install.install(
                    project, addon=addon, codex="mock-codex", runtime=runtime
                )
            self.assertEqual(len(cached), 1)
            adapter = cached[0]["mcpServers"]["gramps_desktop"]
            self.assertEqual(
                adapter["command"], str(Path(install.sys.executable).resolve())
            )
            self.assertEqual(
                adapter["args"], ["server.py", "--runtime-dir", str(runtime)]
            )
            self.assertEqual(adapter["env"], {"GRAMPS_DESKTOP_RUNTIME": str(runtime)})

    def test_failed_registration_preserves_concurrent_transport(self) -> None:
        """Compensation cannot overwrite a transport changed during registration."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            before = (addon / "DesktopControl.py").read_bytes()

            def concurrent(command: list[str], **kwargs: object) -> None:
                """Change the staged transport before reporting registry failure."""
                (source / ".mcp.json").write_text("concurrent transport\n")
                raise subprocess.CalledProcessError(1, command)

            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(
                install.subprocess, "run", side_effect=concurrent
            ), self.assertLogs(
                install.LOG, level="ERROR"
            ):
                with self.assertRaisesRegex(RuntimeError, "preserved for review"):
                    install.install(project, addon=addon, codex="mock-codex")
            self.assertEqual(
                (source / ".mcp.json").read_text(), "concurrent transport\n"
            )
            self.assertEqual((addon / "DesktopControl.py").read_bytes(), before)
            self.assertFalse((project / ".codex/config.toml").exists())

    def test_partial_file_failure_restores_and_retry_succeeds(self) -> None:
        """Restore exact existing bytes and remove newly written owned files."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            before = (addon / "DesktopControl.py").read_bytes()
            original = install.atomic_text
            calls = 0

            def failing(path: Path, text: str) -> None:
                """Fail after the second replacement to exercise compensation."""
                nonlocal calls
                calls += 1
                original(path, text)
                if calls == 2:
                    raise OSError("injected replacement failure")

            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(install.subprocess, "run"):
                with patch.object(install, "atomic_text", side_effect=failing):
                    with self.assertRaises(OSError):
                        install.install(project, addon=addon, codex="mock-codex")
                self.assertEqual((addon / "DesktopControl.py").read_bytes(), before)
                self.assertFalse((addon / "DesktopControl.gpr.py").exists())
                self.assertEqual(
                    (source / ".mcp.json").read_text(), "original transport\n"
                )
                result = install.install(project, addon=addon, codex="mock-codex")
                self.assertTrue(result["local_files_committed"])
                self.assertTrue((addon / "bridge_source.json").exists())

    def test_concurrent_edit_is_preserved(self) -> None:
        """Do not restore over another writer's changed file."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "first", root / "second"
            first.write_text("original")
            original = install.atomic_text

            def concurrent(path: Path, text: str) -> None:
                """Inject an unrelated edit before the next write fails."""
                if path == second:
                    first.write_text("concurrent change")
                    raise OSError("injected failure")
                original(path, text)

            with patch.object(
                install, "atomic_text", side_effect=concurrent
            ), self.assertLogs(install.LOG, level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "preserved for review"):
                    install.write_files({first: "proposed", second: "new"})
            self.assertEqual(first.read_text(), "concurrent change")
            self.assertFalse(second.exists())

    def test_config_edit_during_preparation_prevents_installation(self) -> None:
        """Reject a stale rendered config before registration or loader writes."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            config = project / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text("# original config\n")
            before = (addon / "DesktopControl.py").read_bytes()
            original = install.configure_text

            def concurrent(*args: object, **kwargs: object) -> str:
                """Render from the snapshot, then inject another writer's edit."""
                rendered = original(*args, **kwargs)
                config.write_text("# concurrent config\n")
                return rendered

            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(
                install, "configure_text", side_effect=concurrent
            ), patch.object(
                install.subprocess, "run"
            ) as registry:
                with self.assertRaisesRegex(RuntimeError, "changed concurrently"):
                    install.install(project, addon=addon, codex="mock-codex")
            registry.assert_not_called()
            self.assertEqual(config.read_text(), "# concurrent config\n")
            self.assertEqual((addon / "DesktopControl.py").read_bytes(), before)
            self.assertEqual((source / ".mcp.json").read_text(), "original transport\n")

    def test_explicit_runtime_binds_loader_and_adapter(self) -> None:
        """Persist one absolute discovery path without changing process state."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            runtime = Path(temporary) / "runtime"
            with patch.object(install, "__file__", str(source / "install.py")):
                with self.assertRaises(ValueError):
                    install.install(
                        project, standalone=True, addon=addon, runtime="relative"
                    )
                install.install(project, standalone=True, addon=addon, runtime=runtime)
            self.assertEqual(
                json.loads((addon / "bridge_source.json").read_text())["runtime"],
                str(runtime),
            )
            self.assertEqual(
                json.loads((source / ".mcp.json").read_text())["mcpServers"][
                    "gramps_desktop"
                ]["env"],
                {"GRAMPS_DESKTOP_RUNTIME": str(runtime)},
            )
            configured = tomllib.loads((project / ".codex/config.toml").read_text())
            self.assertEqual(
                configured["mcp_servers"]["gramps_desktop"]["env"][
                    "GRAMPS_DESKTOP_RUNTIME"
                ],
                str(runtime),
            )

    def test_bridge_only_preserves_client_settings_and_transport(self) -> None:
        """Other clients can install the native bridge without Codex access."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            config = project / ".codex/config.toml"
            config.parent.mkdir()
            config.write_text("invalid unrelated configuration [[\n")
            runtime = Path(temporary) / "shared-runtime"
            with patch.object(
                install, "__file__", str(source / "install.py")
            ), patch.object(install.shutil, "which", return_value=None), patch.object(
                install,
                "configure_text",
                side_effect=AssertionError("Codex config read"),
            ), patch.object(
                install.subprocess, "run"
            ) as registry:
                result = install.install(
                    project, addon=addon, runtime=runtime, bridge_only=True
                )
            registry.assert_not_called()
            self.assertTrue(result["bridge_only"])
            self.assertEqual(config.read_text(), "invalid unrelated configuration [[\n")
            self.assertEqual((source / ".mcp.json").read_text(), "original transport\n")
            self.assertEqual(
                json.loads((addon / "bridge_source.json").read_text())["runtime"],
                str(runtime),
            )

    def test_bridge_only_rejects_standalone_mode(self) -> None:
        """Conflicting client modes fail before writing a loader."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            with patch.object(install, "__file__", str(source / "install.py")):
                with self.assertRaisesRegex(ValueError, "bridge-only or standalone"):
                    install.install(
                        project, addon=addon, bridge_only=True, standalone=True
                    )
            self.assertFalse((addon / "bridge_source.json").exists())

    def test_bridge_only_reuses_installed_discovery_and_configures_claude(self) -> None:
        """Native Claude and other clients retain the packaged host's discovery path."""
        with tempfile.TemporaryDirectory() as temporary:
            source, project, addon = self.fixture(Path(temporary))
            runtime = Path(temporary) / "existing-runtime"
            (addon / "bridge_source.json").write_text(
                json.dumps({"runtime": str(runtime)})
            )
            with patch.object(install, "__file__", str(source / "install.py")):
                result = install.install(project, addon=addon, bridge_only=True)
            self.assertEqual(result["runtime"], str(runtime))
            self.assertEqual(
                json.loads((addon / "bridge_source.json").read_text())["runtime"],
                str(runtime),
            )
            claude = json.loads((source / "claude.mcp.json").read_text())[
                "gramps_desktop"
            ]
            self.assertEqual(
                claude["command"], str(Path(install.sys.executable).resolve())
            )
            self.assertEqual(
                claude["args"],
                ["${CLAUDE_PLUGIN_ROOT}/server.py", "--runtime-dir", str(runtime)],
            )
            self.assertEqual((source / ".mcp.json").read_text(), "original transport\n")


if __name__ == "__main__":
    unittest.main()
