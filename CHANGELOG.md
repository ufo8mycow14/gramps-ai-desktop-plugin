# Changelog

## 2.10.1 — 8 October 2026

- Rename the product, package identity, GitHub repository and README to
  **Gramps AI Desktop Plugin**.
- Preserve the previous Gramps Codex Desktop Plugin identity as an installer
  migration alias so existing installations can move safely.
- Refresh public release links, Claude marketplace commands and archive naming.

## 2.10.0 — 8 October 2026

- Rename the product to Gramps Codex Desktop Plugin across documentation,
  native labels, plugin metadata, GitHub links and downloadable packages.
- Migrate existing installations to the new plugin and marketplace identity;
  retain native loader IDs, tool names and standalone transport compatibility.
- Add native Claude Code plugin/marketplace packaging and a separate transport.
- Add absolute-path configuration and shell-quoted registration command exports
  for Claude Code, Kimi CLI, Hermes Agent and OpenCode; support Grok coding through
  OpenCode's xAI provider without changing models or provider credentials.
- Add bridge-only installation without Codex configuration/registration. Reuse
  installed explicit runtime discovery across clients and stage Claude settings.
- Let explicit adapter runtime arguments override inherited environment settings
  before resolving defaults, including malformed relative environment paths.
- Check generated transports from another working directory with all 58 tools;
  document actual client execution as unverified.

## 2.9.0 — 8 October 2026

- Recover verified abandoned local Windows SQLite locks automatically on native
  selected-tree/autoload opening; reuse the shared open tree and retain active
  or uncertain locks. Preserve native close/upgrade handling and release guards
  after cancellation/failure; install all hooks before native autoload.
- Refresh opening hooks when their source changes, even within one bridge
  session; preserve outstanding guards until native opening completes. Close
  plugin-owned SQLite connections and recover abandoned markers after an early
  tree-creation failure.
- Add reviewed native record create/delete/merge batches with detached previews,
  original revisions, native ID allocation, backlink cleanup, independent person
  choices, transitive/implicit merge mappings and guarded receipt rollback.
- Add XML/GEDCOM/CSV/GeneWeb import plans and empty-tree XML restoration, binding
  input bytes, destination and prompt policy. Preserve honest partial/unknown
  receipts, warnings and native state restoration; imports are not atomic.
- Add exact-path SQLite tree creation/open/close, closed-tree rename and removal
  into an explicit preservation directory, with native outcome receipts.
- Add specialised person/place/LDS/styled-note details and alternate-name
  promotion; add five lossy native export formats and CSV option/cycle guards.
- Migrate known plugin identities and compensate interrupted local installer
  writes; support an explicit shared discovery directory. Expand to 58 tools.
- Stage interpreter/discovery settings before Codex caches the plugin; restore
  owned files after registration failure and preserve concurrent transport edits.

## 2.8.0 — 8 October 2026

- Expand the existing secondary tool with typed collection discovery and
  revision-bound add/remove/reorder for names, surnames, addresses, attributes,
  URLs and alternate place names/locations, including nested reference attributes.
- Preserve native blank dates and surname placeholders; reject invalid date
  metadata, injected attachments, wrong classes and stale item/array revisions.
- Add Gregorian/Julian month/year offsets with explicit reject/clamp semantics,
  final-target arithmetic and BCE transitions without year zero.
- Add Dashboard Gramplet instances, columns, movement, collapse, close and
  session restore; derive visible positions after native closes and clean up
  native callbacks/idle resources when content construction fails.
- Keep all 54 tool identities compatible. Verify 232 isolated native checks,
  including 15 new unittest flows, plus package and installation checks.

## 2.7.0 — 7 October 2026

- Add graph, audit, tree metadata, embedded record, navigation and Gramplet tools,
  bringing the catalogue to 54. Preserve recorded links, relationship types,
  ordered bookmarks and revision-bound metadata/record changes.
- Add reviewed private/living/person-filter exports with linked-record closure,
  permitted metadata links and detached relative media paths. Keep backups whole.
- Expose recursive native schemas, stable type codes/custom choices, precision-safe
  dates, complete registry diagnostics and reviewed plugin visibility changes.
- Add profile preference previews/receipts/guarded rollback, with explicit callback
  compensation and persistence limits. Keep legacy parse/single-preference calls.
- Reject graph inspection overflow, ambiguous Gramplets, wrong embedded classes,
  inconsistent date shapes, stale secondary targets and unsafe bridge hiding.
- Extend synthetic native and package/version checks; preserve live session and
  existing batch, tree-metadata and preference receipts during local refresh.
- Retain external compiler, third-party lifecycle, Web authentication and other
  actual GTK-host limitations; no live genealogy or media mutation.

## 2.6.0 — 7 October 2026

- Add `gramps_export`, bringing the plugin to 48 tools: reviewed native XML,
  compressed XML, GEDCOM and XML/media-package exports, plus explicit backups.
- Stage output and bind records, tree metadata, destinations and package media.
  Preserve source media and native database directories. Portable packages use
  safe relative members; missing/remote media requires explicit incomplete
  backup authority and retains its original record path/URL.
- Enable bundled LaTeX and tree-source generation using private JPEG/thumbnail
  assets, native crop semantics and relative references. Preserve source images,
  sibling files and Gramps' shared thumbnail cache. Bind image bytes for all reports.
- Keep automatic tree PDF restricted pending a confined external compiler.
  Source exports do not execute a compiler; native dialogs remain available.
- Verify native export output/restoration, image-preservation/staleness and tree
  source dispatch on synthetic registrations. Individual tree add-ons, live Web
  and other GTK hosts remain unverified.

## 2.5.0 — 7 October 2026

- Add `gramps_batch_attach` and `gramps_batch_file`, bringing the plugin to 47
  tools. Stage 1–200 ordered attachment operations in one native transaction,
  group repeated owners and guard both owner and target revisions.
- Preserve native event roles and birth/death indexes, media crops, repository
  call numbers and nested notes. Require selectors for ambiguous removals.
- List/read the latest 100 session receipts; export complete JSON evidence and
  save reviewed plans for execution at the same local tree location/release.
  Reject stale instructions, changed files and wrong-tree rollback. Receipt
  import and cross-session rollback are not supported.
- Expose native paper, orientation, margins, stylesheet, HTML CSS and document
  generator options. Stage HTML/SVG bundles in a new directory and return every
  generated file's size/hash; preserve existing bundles and failed destinations.
- Verify native TXT/PDF/RTF/ODT/HTML/SVG generation. Automatic LaTeX/tree output
  requires source-media isolation and remains restricted to native dialogs.
- Pass synthetic bulk/archive checks, native integration checks and package
  checks. No live family records or media were changed.

## 2.4.0 — 7 October 2026

- Add dedicated native filters, report automation, media management, authenticated
  Gramps Web access and scoped push/pull synchronisation, bringing the plugin to
  45 tools. Retain the released atomic bulk-edit/receipt/rollback workflow.
- Preserve native filter definitions and backups with store revisions; reject
  unavailable rules, dependency cycles and deletion of depended-on filters.
- Validate report options and bind output plans to records/destinations; stage
  generation so failures preserve existing files.
- Inspect scoped missing media paths and metadata; relink explicitly selected
  files through reviewed transactions without moving or downloading them.
- Guard sync with shared handles/IDs, tree identity, matching Gramps releases,
  native references, reciprocal links and ancestry checks. Include previewed
  dependency guard updates; report queued writes as pending.
- Add a dependency-free Python installer and PowerShell wrapper for Gramps 6.0/6.1
  with Windows/Linux/macOS paths and explicit overrides.
- Exercise native workflows on isolated synthetic data and Web operations against
  a mock API. Live Web, Linux/macOS GTK and Gramps 6.0 desktop remain unverified.

## 2.3.1 — 7 October 2026

- Set the MCP stdio protocol to UTF-8 explicitly on Windows, preserving Unicode
  tool descriptions and genealogy record text. Bulk-edit behaviour is unchanged.

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

First public release of the Gramps Codex Desktop Plugin.

- Package the 36-tool desktop/database integration as a standalone repository.
- Discover Python or accept an explicit Python executable and workspace path.
- Include a local Codex marketplace and general workflow skill.
- Preserve TOML configuration sections with trailing comments during migration.
- Return a JSON-RPC error for malformed parameters while keeping stdio alive.
- Require an explicit Gramps executable or `GRAMPS_EXECUTABLE` for launch.
- Document tested coverage, installation, privileged access and compatibility limits.

The compatible bridge API remains 2.1.0. Local verification receipts and user data
are excluded from public packages.
