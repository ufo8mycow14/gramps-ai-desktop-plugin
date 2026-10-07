# Changelog

## 2.3.0 — 7 October 2026

- Add `gramps_batch`, bringing the plugin to 40 tools.
- Preview 1–200 existing-record updates with complete before/proposed snapshots.
- Apply each reviewed batch in one native transaction with current revisions and
  a matching preview plan; preserve identifiers and reciprocal family routes.
- Return exact before/after change receipts and the native undo label.
- Preview and apply receipt rollback while rejecting intervening record changes.
- Pass 18 isolated synthetic checks, including injected transaction failure and
  complete rollback; no live family records were changed.

## 2.2.1 — 7 October 2026

- Expand the integration to 39 tools with native menu paths, targets, revisions
  and table-cell discovery.
- Resolve widget-local popup action groups and distinguish submenu headings.
- Normalise report/tool IDs using Gramps' native action naming.
- Complete native table text/combo editing lifecycles; reject disabled cells.
- Add file/folder, colour, font and calendar control operations with readback.
- Preserve invalid-input state for toggles, combo choices and calendar values.
- Open the native tag editor, alongside the existing nine record editors.
- Fix plain GTK text-buffer editing.
- Check the added controls in an isolated instance of the installed GTK runtime.

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
