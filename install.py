"""Portable installer: no package installation, tree access or application restart."""

import argparse
from collections.abc import Callable
import json
import logging
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from configure import configure_text
from clients import shared_runtime
from platform_paths import addon_dir, SUPPORTED_GRAMPS

LOG = logging.getLogger(__name__)


def atomic_text(path: Path, text: str) -> None:
    """Replace one owned text file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temp, path)


def write_files(
    changes: dict[Path, str],
    *,
    staged_path: Path | None = None,
    on_staged: Callable[[], None] | None = None,
    expected_snapshots: dict[Path, bytes | None] | None = None,
) -> None:
    """Restore owned file snapshots if a local installation write fails."""
    if on_staged is not None and staged_path not in changes:
        raise ValueError("Registration requires a staged transport file")
    snapshots = {path: path.read_bytes() if path.exists() else None for path in changes}
    for path, expected in (expected_snapshots or {}).items():
        if path not in snapshots or snapshots[path] != expected:
            raise RuntimeError("Installation file changed concurrently: " + str(path))
    written: list[Path] = []
    try:
        for path, text in changes.items():
            current = path.read_bytes() if path.exists() else None
            if current != snapshots[path]:
                raise RuntimeError(
                    "Installation file changed concurrently: " + str(path)
                )
            written.append(path)
            atomic_text(path, text)
            if path == staged_path and on_staged is not None:
                on_staged()
                if path.read_bytes() != text.encode("utf-8"):
                    raise RuntimeError("Staged transport changed during registration")
    except Exception:
        residual = []
        for path in reversed(written):
            try:
                current = path.read_bytes() if path.exists() else None
                if current not in (snapshots[path], changes[path].encode("utf-8")):
                    residual.append(str(path))
                    continue
                before = snapshots[path]
                if before is None:
                    if path.exists():
                        path.unlink()
                elif current != before:
                    temporary = path.with_name(path.name + ".rollback.tmp")
                    temporary.write_bytes(before)
                    os.replace(temporary, path)
                temporary = path.with_name(path.name + ".tmp")
                if temporary.exists() and temporary.read_bytes() == changes[
                    path
                ].encode("utf-8"):
                    temporary.unlink()
            except OSError:
                residual.append(str(path))
        if residual:
            LOG.error(
                "Installation compensation needs attention for: %s", ", ".join(residual)
            )
            raise RuntimeError(
                "Installation failed; changed files preserved for review: "
                + ", ".join(residual)
            )
        raise


def install(
    project: Path | str,
    version: str = "6.1",
    standalone: bool = False,
    addon: Path | str | None = None,
    dry_run: bool = False,
    codex: Path | str | None = None,
    runtime: Path | str | None = None,
    bridge_only: bool = False,
) -> dict[str, Any]:
    """Register the package and install recoverable local loader/configuration."""
    source = Path(__file__).resolve().parent
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError("The target workspace must exist")
    if version not in SUPPORTED_GRAMPS:
        raise ValueError("Supported installer targets are Gramps 6.0 and 6.1")
    if bridge_only and standalone:
        raise ValueError("Choose bridge-only or standalone installation")
    target = (Path(addon) if addon else addon_dir(version)).resolve()
    runtime_path = shared_runtime(
        Path(runtime) if runtime is not None else None, target
    )
    marketplace = json.loads(
        (source / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (source / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    identity = manifest["name"] + "@" + marketplace["name"]
    config = project / ".codex/config.toml"
    original_config = (
        config.read_bytes() if not bridge_only and config.exists() else None
    )
    original = original_config.decode("utf-8") if original_config is not None else ""
    updated = (
        original
        if bridge_only
        else configure_text(
            original,
            standalone,
            Path(sys.executable),
            source / "server.py",
            identity,
            runtime_path,
        )
    )
    for name in ("DesktopControl.py", "DesktopControl.gpr.py"):
        existing = target / name
        if existing.exists() and not any(
            marker in existing.read_text(encoding="utf-8")
            for marker in ("bridge_source.json", "Desktop MCP Control")
        ):
            raise ValueError("Unrelated add-on preserved: " + str(existing))
    for file in source.glob("*.py"):
        compile(file.read_text(encoding="utf-8"), file.name, "exec")
    executable = codex or shutil.which("codex")
    if not bridge_only and not standalone and not executable:
        raise ValueError(
            "Codex CLI with plugin commands is required; otherwise use --standalone"
        )
    result = {
        "addon": str(target),
        "config": str(config),
        "plugin": identity,
        "gramps_target": version,
        "dry_run": dry_run,
        "standalone": standalone,
        "bridge_only": bridge_only,
        "runtime": str(runtime_path),
    }
    if dry_run:
        return result
    changes = {}
    for name in ("DesktopControl.py", "DesktopControl.gpr.py"):
        text = (source / name).read_text(encoding="utf-8")
        if name.endswith(".gpr.py"):
            text = re.sub(
                r"(gramps_target_version\s*=\s*)(['\"])6\.[01]\2",
                lambda match: match[1] + repr(version),
                text,
                count=1,
            )
        changes[target / name] = text
    changes[target / "bridge_source.json"] = (
        json.dumps(
            {
                "source": str(source / "desktop_bridge.py"),
                "version": manifest["version"],
                **({"runtime": str(runtime_path)} if runtime_path is not None else {}),
            },
            indent=2,
        )
        + "\n"
    )
    transport: dict[str, Any] = {
        "mcpServers": {
            "gramps_desktop": {
                "command": str(Path(sys.executable).resolve()),
                "args": ["server.py"],
                "cwd": ".",
                "startup_timeout_sec": 15,
                "tool_timeout_sec": 120,
            }
        }
    }
    if runtime_path is not None:
        transport["mcpServers"]["gramps_desktop"]["args"] += [
            "--runtime-dir",
            str(runtime_path),
        ]
        transport["mcpServers"]["gramps_desktop"]["env"] = {
            "GRAMPS_DESKTOP_RUNTIME": str(runtime_path)
        }
    transport_path = source / ".mcp.json"
    if not bridge_only:
        changes = {transport_path: json.dumps(transport, indent=2) + "\n", **changes}
    # Claude resolves the cached plugin root; the interpreter and discovery path
    # must agree with the native loader even in a packaged Windows host.
    changes[source / "claude.mcp.json"] = (
        json.dumps(
            {
                "gramps_desktop": {
                    "type": "stdio",
                    "command": str(Path(sys.executable).resolve()),
                    "args": [
                        "${CLAUDE_PLUGIN_ROOT}/server.py",
                        "--runtime-dir",
                        str(runtime_path),
                    ],
                }
            },
            indent=2,
        )
        + "\n"
    )

    def register() -> None:
        """Cache the staged transport before changing the native loader."""
        if executable is None:
            raise ValueError("Codex CLI is required for plugin registration")
        subprocess.run(
            [executable, "plugin", "marketplace", "add", str(source), "--json"],
            check=True,
        )
        subprocess.run([executable, "plugin", "add", identity, "--json"], check=True)

    if updated != original:
        changes[config] = updated
    write_files(
        changes,
        staged_path=None if bridge_only else transport_path,
        on_staged=None if bridge_only or standalone else register,
        expected_snapshots={config: original_config} if config in changes else None,
    )
    result["local_files_committed"] = True
    result["registry_failure_policy"] = (
        "Registration precedes loader writes; a registered package may remain after failure and rerunning is supported"
    )
    return result


def main() -> None:
    """Install using explicit target arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--gramps-version", choices=SUPPORTED_GRAMPS, default="6.1")
    parser.add_argument(
        "--addon-dir", type=Path, help="Override the native Gramps add-on directory"
    )
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument(
        "--bridge-only",
        action="store_true",
        help="Install the Gramps add-on without Codex registration or settings",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Bind adapter and bridge to one explicit discovery directory",
    )
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required")
    print(
        json.dumps(
            install(
                args.project,
                args.gramps_version,
                args.standalone,
                args.addon_dir,
                args.dry_run,
                runtime=args.runtime_dir,
                bridge_only=args.bridge_only,
            )
        )
    )


if __name__ == "__main__":
    main()
