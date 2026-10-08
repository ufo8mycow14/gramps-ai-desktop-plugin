"""Build an allowlisted source-only release; never copy a workspace recursively."""

import argparse
import json
from pathlib import Path
import shutil
import zipfile

FILES = (
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CLIENTS.md",
    ".gitignore",
    ".codex-plugin/plugin.json",
    ".agents/plugins/marketplace.json",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "skills/gramps/SKILL.md",
    "server.py",
    "desktop_bridge.py",
    "support.py",
    "ui_support.py",
    "batch_support.py",
    "batch_files.py",
    "analysis_support.py",
    "tree_support.py",
    "secondary_support.py",
    "date_support.py",
    "preference_support.py",
    "navigation_support.py",
    "workflow_support.py",
    "native_filters.py",
    "native_reports.py",
    "report_output.py",
    "report_media.py",
    "native_exports.py",
    "native_imports.py",
    "database_support.py",
    "native_batch.py",
    "special_details.py",
    "lock_support.py",
    "web_support.py",
    "platform_paths.py",
    "install.py",
    "clients.py",
    "verify_batch.py",
    "verify_integrations.py",
    "verify_expansion.py",
    "DesktopControl.py",
    "DesktopControl.gpr.py",
    "configure.py",
    "install.ps1",
    "verify_package.py",
    "verify_support.py",
    "verify_menu_support.py",
    "package_release.py",
    "test/detail_lifecycle_test.py",
    "test/installer_test.py",
    "test/clients_test.py",
    "test/lock_recovery_test.py",
    "test/native_lock_opening_test.py",
    "test/native_batch_test.py",
    "test/native_imports_test.py",
    "test/database_lifecycle_test.py",
    "test/native_exports_test.py",
    "test/special_details_test.py",
)


def build(destination):
    source = Path(__file__).resolve().parent
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Use a fresh destination; existing contents are preserved")
    for name in FILES:
        if not (source / name).is_file():
            raise ValueError("Required public file missing: " + name)
    destination.mkdir(parents=True)
    for name in FILES:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    # Installer-generated local paths must never enter the source release.
    transport = {
        "mcpServers": {
            "gramps_desktop": {
                "command": "python",
                "args": ["server.py"],
                "cwd": ".",
                "startup_timeout_sec": 15,
                "tool_timeout_sec": 60,
            }
        }
    }
    (destination / ".mcp.json").write_text(
        json.dumps(transport, indent=2) + "\n", encoding="utf-8"
    )
    # Both host transports must be portable; installed local paths stay private.
    claude_transport = {
        "gramps_desktop": {
            "type": "stdio",
            "command": "python",
            "args": ["${CLAUDE_PLUGIN_ROOT}/server.py"],
        }
    }
    (destination / "claude.mcp.json").write_text(
        json.dumps(claude_transport, indent=2) + "\n", encoding="utf-8"
    )
    version = json.loads((destination / ".codex-plugin/plugin.json").read_text())[
        "version"
    ]
    archive = destination.parent / ("gramps-ai-desktop-plugin-" + version + ".zip")
    if archive.exists():
        raise ValueError("Existing archive preserved; use another destination parent")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in (*FILES, ".mcp.json", "claude.mcp.json"):
            bundle.write(destination / name, "gramps-ai-desktop-plugin/" + name)
    return {
        "version": version,
        "files": len(FILES) + 2,
        "repository": str(destination),
        "archive": str(archive),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    print(json.dumps(build(parser.parse_args().destination)))
