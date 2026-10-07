# Changelog

## 2.1.2 — 7 October 2026

First public release of the Gramps Desktop plugin.

- Package the 36-tool desktop/database integration as a standalone repository.
- Discover Python or accept an explicit Python executable and workspace path.
- Include a local Codex marketplace and general workflow skill.
- Preserve TOML configuration sections with trailing comments during migration.
- Return a JSON-RPC error for malformed parameters while keeping stdio alive.
- Require an explicit Gramps executable or `GRAMPS_EXECUTABLE` for launch.
- Document tested coverage, installation, privileged access and compatibility limits.

The compatible bridge API remains 2.1.0. Local verification receipts and user data
are excluded from public packages.
