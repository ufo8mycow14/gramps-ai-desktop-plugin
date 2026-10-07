# Gramps Desktop plugin

I built this plugin to let Codex work with the **running Gramps desktop**:
its windows, menus, native editors and already-open family tree. I package it as
a Codex plugin with a Gramps startup add-on and a dependency-free Python adapter.
MCP supplies the tool transport and standalone-client integration.

**Release: 2.5.0 · 47 tools · GPL-2.0-or-later**

[Download the release](https://github.com/ufo8mycow14/gramps-desktop-plugin/releases/latest)
· [Report a problem](https://github.com/ufo8mycow14/gramps-desktop-plugin/issues)

This is an independent community project. It is not an official Gramps add-on
or endorsed by the Gramps maintainers. The tested desktop is **Windows, Gramps
AIO64-6.1.0-beta2-1, GTK 3.24.52**. I provide installation targets for Gramps
6.0/6.1 and Windows/Linux/macOS paths; other desktop hosts remain unverified.

## What it supports

| Area | Supported operations | Scope |
| --- | --- | --- |
| Connection | Health, version/readiness diagnostics, session status and launching an explicitly located Gramps executable | Reuses the running bridge and open database |
| Windows and menus | Discover full native menu paths, targets, action scopes and enabled states; activate current menu paths and buttons; capture window screenshots | Covers main menus, GTK context menus and widget-local popups; stale menu revisions are rejected |
| Navigation and controls | Switch native views; inspect/edit table cells and combo cells; read/set selections; notebook pages; text, toggles, numbers, dates, colours, fonts and file/folder choices | Uses native editor callbacks and control bounds; individual add-on interfaces can differ |
| Native editing screens | Open existing or unsaved people, families, events, places, sources, citations, repositories, media, notes and tags | Uses normal Gramps editors and their Save/Cancel controls |
| Structured records | Read schemas and named fields; search by text/field filters; create, update and delete native records | Ten record kinds, including tags; preview by default and revision checks for existing records |
| Bulk editing | Preview/apply record patches and ordered tag/reference attachments; list/export receipts and save plans | 1–200 operations in one transaction; owner/target revisions and reviewed plans; tree-bound rollback |
| Custom filters | Discover native rules; build/run definitions; preview/save/delete named filters | Nine namespaces; profile-wide XML store with revision checks and backups; tags use record search |
| Report automation | Native text, drawing and Graphviz output; paper/orientation/margins/style/CSS and generator options; HTML/SVG bundles | Reviewed destinations; automatic LaTeX/tree generation restricted pending source-media isolation; native dialogs available |
| Media management | Inspect missing paths and file metadata; search explicit directories; preview/relink records | Existing local files; transactional receipts; no moves or downloads |
| Gramps Web | Authenticated record/search/backlink/history/schema/task access and scoped push/pull | Explicit server, shared handles/IDs, matching Gramps releases and reviewed tree-bound plans |
| Relationships | Read parents, partners and children; add/remove parent and child memberships | Reciprocal person/family updates in a transaction; ancestry-cycle checks |
| References | Read outgoing links/backlinks; attach/detach citations, notes, tags, events, media, repository references and citation sources | Supported combinations follow native record schemas |
| Merging | Compare records and preview/apply native merges | Nine record kinds; both current revisions required for apply; tags are excluded |
| Research context | Read scoped records, citations and sources; resolve media paths/metadata; parse/display dates | Reads recorded assertions; it does not establish historical identity or prove a relationship |
| History | Read undo/redo history and perform native undo/redo | Shares the desktop application's history, including manual edits |
| Workflows | Open import, export, backup, tree manager, history, preferences, add-on, report and tool dialogs | Final actions use the native dialogs; dependencies and file/service access still apply |
| Preferences and add-ons | Discover installed reports, tools, importers/exporters, views, gramplets and backends; read/set native preferences | Discovery does not install missing dependencies |
| Advanced control | Execute trusted Python inside Gramps on its GTK main thread; inspect long-running operation receipts | Privileged local access; not a sandbox |

The ten structured record kinds are `person`, `family`, `event`, `place`,
`source`, `citation`, `repository`, `media`, `note` and `tag`.

### Menus, tools and reports

I expose the native menu hierarchy and installed report/tool actions, including
context menus and widget-local popups. The plugin reads each action's path,
target, scope and enabled state, then invokes Gramps' own action. New menu
discovery is required after a menu revision changes.

Native dialog control includes text fields, tables, editable combo cells,
selections, tabs, toggles, numeric values, calendars, colours, fonts and
file/folder choosers. All ten record kinds have native editor access, alongside
structured record operations and trusted Python access for advanced work.

Read-only discovery in the tested installation resolved **155 actionable menu
entries, 55 GUI tools and 62 GUI reports**. These are overlapping inventories of
available entry points; the visible menu inventory depends on the current view,
open tree and dialogs.
They establish native action routing; I have not verified the final execution
of every report, tool or third-party add-on. A workflow can still require an
external dependency, service access or a particular database state.

## Requirements

- Gramps **6.0 or 6.1** installed and working normally. Windows 6.1 is tested;
  Linux/macOS installation paths are covered by offline checks.
- Python **3.11 or newer** for the external adapter and installer. No pip packages
  are required. Gramps supplies its own GTK and genealogy modules inside the app.
- A Codex CLI that supports `codex plugin marketplace` and
  `codex plugin add`. Plugin installation was checked with CLI **0.157.0**.
- For another MCP client, use the standalone installation and configure its
  stdio transport as described below. That client's integration is not verified.

## Install

Save any work in Gramps first. Download and extract the release into a permanent
folder, or clone it:

```powershell
git clone https://github.com/ufo8mycow14/gramps-desktop-plugin.git
Set-Location gramps-desktop-plugin
.\install.ps1 -Project 'C:\path\to\your\workspace'
```

The `-Project` directory must exist. Omit it to use this checkout as the workspace.
If Python is not on PATH, provide its actual executable:

```powershell
.\install.ps1 -Python 'C:\path\to\python.exe' -Project 'C:\path\to\your\workspace'
```

The same installer is available through Python on Linux/macOS or Windows:

```sh
python3 install.py --project /absolute/path/to/workspace --gramps-version 6.0
```

On Windows, select 6.0 with `-GrampsVersion 6.0`; the default is 6.1.
`--dry-run` / `-DryRun` reports the chosen paths without writing. The installer:

1. Checks Python and compiles the adapter/bridge source.
2. Installs the startup loader under the selected native add-on directory:
   `%APPDATA%\gramps\gramps61\plugins\DesktopMCPControl` on Windows, or
   `$XDG_DATA_HOME/gramps/gramps61/plugins/DesktopMCPControl` on Linux/macOS
   (default `~/.local/share`). Version 6.0 uses `gramps60`. `GRAMPSHOME` and
   `--addon-dir` / `-AddonDirectory` provide explicit overrides.
3. Writes a source locator and generates the local `.mcp.json` with the selected
   Python executable.
4. Registers this checkout's marketplace and installs
   `gramps-desktop@gramps-desktop-plugins`.
5. Enables the plugin in the target workspace's `.codex/config.toml`, preserving
   unrelated settings and removing a duplicate `gramps_desktop` standalone entry.

Keep the checkout in place: the Gramps loader points to its bridge source.
Moving it requires rerunning installation from the new location. Save your work,
close and reopen Gramps normally, then reconnect the client's tools or start a
new chat to refresh tool discovery. Allow Gramps startup dialogs to finish.

Run the read-only diagnostic:

```powershell
python .\server.py --call gramps_health
```

Use the same Python executable you supplied to the installer. A ready connection
reports `connected: true`, `version_match: true` and `ready: true`. Do not share
the connection discovery file: it contains the local bearer token.

If an older local package is installed under another marketplace name, disable
that older package before enabling this one so the client has one desktop server.
Do not force-unlock a tree or launch repeated Gramps processes to obtain access.

### Standalone MCP client

```powershell
.\install.ps1 -Standalone -Project 'C:\path\to\your\workspace'
```

Python equivalent: `python3 install.py --standalone --project /absolute/path/to/workspace`.

This installs the same Gramps loader, disables this plugin in the selected
workspace and adds one standalone Codex MCP entry. For another client, configure
an absolute Python executable and an absolute path to `server.py`, for example:

```json
{
  "mcpServers": {
    "gramps_desktop": {
      "command": "C:/path/to/python.exe",
      "args": ["C:/path/to/gramps-desktop-plugin/server.py"]
    }
  }
}
```

### Launching Gramps

Open Gramps normally, or pass the actual installed executable to `gramps_launch`:

```json
{"executable": "C:/Program Files/your-Gramps-installation/grampsw.exe"}
```

An optional `GRAMPS_EXECUTABLE` environment variable supplies the default.
The launch tool first checks for a connected bridge and reuses it.

## Using the plugin

I recommend starting with `gramps_health`, `gramps_status` and
`gramps_capabilities`, then reading the intended records or inspecting the visible
windows. Useful requests include:

- “Show the open Gramps windows and available menu actions.”
- “Find these people and show their recorded sources and relatives.”
- “Open this person's native editor so I can review it.”
- “Preview this note update and show the changed fields before applying it.”
- “Compare these two records and their references before I decide about a merge.”
- “Open the backup dialog.”
- “List the installed reports and tools, then open this report's options dialog.”

For structured changes, first read the schema and current object. Preview the
change with `apply: false` (the default), inspect the proposed result, then apply
with the required current revision. Stale revisions are rejected. Family links
use `gramps_family_member` so both sides stay consistent. Deletion rejects
referenced records; merges use Gramps' native merge implementation.

Native editor Save/OK buttons commit through Gramps. A tool opening an editor or
workflow dialog does not complete its final operation. Widget IDs expire when
controls are destroyed; refresh discovery after UI changes. If an operation
times out, query `gramps_job` using its returned ID before repeating it.

For menus, use `gramps_menus`, then pass the observed `path` and
`expected_revision` to `gramps_menu`. Retain `root_id` for a context menu or
widget-local popup. Submenu headings list children; they are not launch actions.
Report/tool discovery exposes the actual normalised action name, including IDs
containing underscores.

For tables, use `gramps_cells` with the row path before editing. `edit_cell`
accepts `{path, column, renderer, text}`; `choose_cell` uses `choice_path` from
the observed combo choices; `toggle_cell` uses the same cell coordinates.
Show the table and select its notebook page first. Disabled or hidden cells are
rejected. Cell edits complete the native editing lifecycle.

File controls support `choose_files`, `set_folder` and `set_filename`.
Multiple selections use one directory. GTK loads directory contents asynchronously:
inspect the returned `file_selection.state` and refreshed `filenames` before
confirming a dialog. A `pending` receipt is not confirmed selection. Save-name
selection does not itself write a file. Colour/font/calendar controls use
`set_color`, `set_font` and `set_calendar`.

Some Gramps tools process data, write files or contact services immediately in
their constructor. Inspect a tool's purpose and apply the current task's authority
before launching it; there is no universal safe options screen.

### Reviewable bulk editing

I added `gramps_batch` for **1–200 existing-record updates in one native Gramps
transaction**. All ten record kinds are supported. Each update supplies `kind`,
`handle`, `expected_revision` and a named-field `patch`.

1. Read each target with `gramps_object` to obtain its current revision.
2. Call `gramps_batch` with `changes` and `apply: false` to inspect the complete
   `before` and `proposed` records and returned `plan_revision`.
3. Apply that same batch with `apply: true` and `expected_plan` set to the preview's
   `plan_revision`. Missing/stale revisions or a changed preview reject the batch.
4. Keep the returned `receipt_id`, full `before`/`after` snapshots and undo label.
   The receipt describes what was actually saved.

For rollback, call `gramps_batch` with `operation: "rollback"` and the receipt ID
to preview restoration. Apply with `expected_plan` set to its `plan_revision`.
Rollback requires every affected record to retain its saved revision; later edits
are preserved by rejecting a stale receipt. Receipts are retained for the current
Gramps session, with the latest 100 available for retrieval and rollback. Rollback
also checks the original tree binding; legacy receipts without that binding are
readable/exportable but cannot be rolled back.

`gramps_batch_attach` previews **1–200 ordered attachment operations**. Each
supplies the owner's `kind`, `handle`, `expected_revision`, the target's
`target_kind`, `target_handle`, `target_revision` and `operation` (`add`/`remove`).
Repeated owners share one staged record and one commit. All owner operations use
the original owner revision. Apply with the preview's `expected_plan`.

Supported native combinations include tags, notes, sourced citations, events,
media, source repository references and citation sources. Optional
`reference_patch` preserves event roles, crop rectangles and call numbers.
Distinct references to one target remain distinct. Ambiguous removal requires a
matching `reference_patch` or explicit `all_references: true`. Person birth/death
indexes and family event roles follow Gramps conventions; detaching a top-level
note preserves notes on child references/attributes. No-op attachment batches
write nothing and return no receipt. Family memberships use `gramps_family_member`.

### Saved plans and exported receipts

Use `gramps_batch` with `operation: "receipts"` to list session receipts and
`operation: "receipt"` plus `receipt_id` to retrieve exact saved snapshots.
`gramps_batch_file` provides explicit local JSON archives:

- `save_plan`: supply `batch_type` (`update`/`attachments`), `changes`, optional
  `label` and an absolute `.json` `file_path` in an existing directory.
- `run_plan`: preview a saved plan, then apply with its `expected_plan` and
  `expected_file_revision`. Records and instructions must still match.
- `export_receipt`: supply `receipt_id` and `file_path` to archive the full receipt.
- `inspect`: read and check archive integrity without changing files or records.

Saving/exporting also requires preview followed by `expected_plan` and
`expected_file_revision`; existing files require `overwrite: true`. Files are
limited to 16 MiB. Plans bind backend, canonical tree save path, native database
ID and Gramps major/minor release. This is local tree-location identity, not a
portable UUID: plans can survive a session restart at that location but reject
another tree, relocation, release change or stale records. In-memory plans are
session-only and cannot be saved. Receipt exports are durable evidence; receipt
import and cross-session rollback are not implemented. Integrity hashes detect
changed content; they do not authenticate an archive's author. Review its changes.

Bulk patches preserve identifiers and cannot directly set change timestamps.
Native commits update timestamps normally. Reciprocal family
relationships use `gramps_family_member`. Native Save/OK edits and bulk updates
share Gramps' database history. I reject bulk writes while native dialogs are open.

I verified **55 isolated bulk/archive checks** using a synthetic SQLite `:memory:`
database, including mixed-record updates, zero-write previews, stale/invalid batch
rejection, injected transaction failure with complete rollback, saved change
receipts, reference selectors, saved-plan reuse and guarded restoration. No live
family records were changed.

### Custom filters

I expose native rules through `gramps_filter` with `operation: "rules"` and a
record `kind`. Build a `definition` containing `name` and ordered `rules`, each
with its observed `class` and string `values`. `logical_op` accepts `and`, `or`
or `one`; rules can use their supported regex/case flags. `run` evaluates a
definition or saved name and returns paginated matches without editing records.

`list` and `get` read saved definitions. Preview `save` or `delete`, then apply
with the returned `store_revision` as `expected_revision`. The store belongs to
the Gramps profile/version and affects all its trees. I preserve a byte backup,
whitespace and native definitions; unavailable rules, missing dependencies,
cycles and deletion of depended-on filters are rejected. Tags use `gramps_find`.

### Report automation

Use `gramps_report` with `list`, then `options` and the observed `report_id`.
Native options expose types, choices and bounds. Preview `run` with an absolute
`output_path`, matching extension and installed `format`; apply using the returned
`plan_revision` as `expected_plan`. Existing destinations require `overwrite: true`.

Use `document` for observed `paper`, `orientation` (`portrait`/`landscape`),
`margins_cm` (`left`/`right`/`top`/`bottom`), `style` and an existing absolute
HTML `css_path`. `document_options` accepts the selected generator's observed
choices, such as SVG `svg_background`. Native types, choices and usable page
area are validated.

HTML/SVG require `bundle: true` and an output inside a **new directory** under an
existing parent, for example `/exports/new-summary/summary.html`. Companions are
published before the main file; `artifacts` lists every file's relative path,
size and SHA-256. Existing bundle directories are preserved; bundle overwrite
is unsupported.

I bind record revisions, effective option/document settings, style/configuration
file revisions and the existing destination to
the preview. Generation uses a temporary file; failed generation preserves the
destination. Successful receipts include size and SHA-256. Available text/drawing
formats depend on installed document generators; I verified TXT, PDF, RTF, ODT,
HTML and drawing SVG. Graphviz needs its native dependencies. Automatic LaTeX
and tree generation is restricted because native converters can write beside
source media; use `open` and the native dialog. The listing/options distinguish
automatic generation support from available formats.

### Media management

`gramps_media_manage` with `inspect` returns scoped media records, resolved paths,
existence, MIME type, size and modification time. `search_roots` searches explicitly
selected directories for filename candidates, with a 10,000-entry bound and
`search_truncated` readback. Filename matches are candidates for review.

Preview `relink` with explicit existing absolute paths, handles and current
revisions. Apply using `expected_plan`; `refresh_mime: true` also updates inferred
MIME metadata. Relinking uses the bulk transaction/receipt mechanism and supports
its guarded rollback. I do not move, rename, download or delete media files.

### Gramps Web and synchronisation

Set `GRAMPS_WEB_URL` in the adapter's local environment before starting its
transport. Authenticate with `GRAMPS_WEB_TOKEN`, `GRAMPS_WEB_SYNC_TOKEN`, or
`GRAMPS_WEB_USERNAME` and `GRAMPS_WEB_PASSWORD`. Keep credentials out of chat,
records and source control. Remote servers require HTTPS; loopback HTTP is allowed.
`gramps_web` supports `status`, `list`, `get`, `search`, `history`, `schema` and
`task`, including optional backlinks and pagination.

For `gramps_web_sync`, explicitly select `push` or `pull` and 1–200 existing
records by `kind` and shared native `handle`. Matching Gramps IDs and matching
desktop/server major/minor releases are required. Review both sides in the preview;
apply with its `plan_revision` as `expected_plan` and its `tree_id` as
`expected_tree_id`. Changed records, tree identity or server invalidate that plan.

I validate native references, reciprocal relationships and destination ancestry.
Pushes use the Web API's guarded transaction with `force=false`. Dependencies
receive explicitly previewed no-op guard updates in the same transaction; their
native timestamps/history may advance. Pulls use one native desktop transaction
with before/after receipts. No media files are transferred or identities inferred.
Adds/deletes and whole-tree operations use `operation: "open_native"` with the
installed Gramps Web Sync add-on and its own controls.

A `pending` result means the server accepted a task. Inspect its `task_id` through
`gramps_web` with `operation: "task"`; acceptance is not completed synchronisation.
After an uncertain write outcome, inspect task/history before retrying. I verified
this adapter against a local mock API; a live authenticated Web server remains
untested. API 3.23.1 targets Gramps 6.0, so it cannot sync with a 6.1 desktop.

## All 47 tools

| Group | Tools |
| --- | --- |
| Connection | `gramps_launch`, `gramps_status`, `gramps_health`, `gramps_capabilities` |
| Desktop | `gramps_windows`, `gramps_widgets`, `gramps_widget`, `gramps_menus`, `gramps_menu`, `gramps_cells`, `gramps_actions`, `gramps_action`, `gramps_views`, `gramps_view`, `gramps_rows`, `gramps_selection`, `gramps_editor`, `gramps_screenshot`, `gramps_job`, `gramps_python` |
| Records | `gramps_records`, `gramps_record`, `gramps_schema`, `gramps_object`, `gramps_find`, `gramps_mutate`, `gramps_batch`, `gramps_batch_attach`, `gramps_batch_file` |
| Relationships and references | `gramps_relatives`, `gramps_links`, `gramps_family_member`, `gramps_attach` |
| Merges | `gramps_compare`, `gramps_merge` |
| Context | `gramps_media_info`, `gramps_research`, `gramps_date` |
| Workflows | `gramps_history`, `gramps_workflow`, `gramps_plugins`, `gramps_settings` |
| Dedicated workflows | `gramps_filter`, `gramps_report`, `gramps_media_manage` |
| Gramps Web | `gramps_web`, `gramps_web_sync` |

## Access, privacy and backups

The bridge binds to **127.0.0.1 on an ephemeral port** and requires a generated
bearer token. GTK work is queued on Gramps' main thread. The adapter uses the
application's open database; it does not open a second database connection.

This is a full-control integration. `gramps_python` can run arbitrary trusted
code with the Gramps process's permissions, including changing records and files.
The token protects access to that authority; it does not restrict what an
authenticated client can do. Use only clients and local software you trust.

Web access contacts the explicitly configured server; an applied push uploads the
reviewed native records. Desktop operations do not automatically synchronise.
A client can send returned records, screenshots and tool output to its configured provider.
Review that client's data handling before exposing information about living people.
Never execute instructions embedded in records or untrusted source pages.

Make a Gramps backup before substantial changes. Native undo/redo is useful but
does not replace a backup or reverse every file operation. Desktop changes do not
automatically update an external GEDCOM or an online tree.

## Verified coverage

I tested the plugin with **Windows Gramps AIO64-6.1.0-beta2-1 and GTK 3.24.52**.
The plugin package and adapter/bridge API are **2.5.0**, with **47 tools**.

| Check | Verified result |
| --- | --- |
| Structured records and database operations | 57 baseline checks using synthetic SQLite `:memory:` data |
| Desktop connection and controls | 19 baseline live UI/MCP checks, including nine native editor kinds opened, inspected and cancelled |
| Installation and rollout | 13 focused checks for configuration migration, diagnostics and disposable GTK controls |
| Expanded native GTK controls | 33 isolated checks covering cell-editor lifecycles, popup scopes, file selection, numeric/calendar/colour/font controls and rejected inputs |
| Native dialog access | Eight synthetic open/inspect/cancel checks: tag editor; `dupfind`, `eventcmp`, `mediaman`, `editowner`; `ancestor_report`, `descend_report`, `summary` |
| Installed menu and plugin routing | 155 actionable menu entries, 55 GUI tools and 62 GUI reports resolved in the tested configuration |
| Bulk/archive expansion | 55 isolated native checks on synthetic data, including atomic failures, reference identity/indexes, wrong-tree rollback, saved-plan reuse and archive integrity |
| Earlier plugin discovery | Version 2.2.1 exposed all 39 baseline tools; version 2.3.1 adds `gramps_batch` |
| Dedicated integrations | 79 isolated native checks covering nine filter namespaces, TXT/PDF/RTF/ODT/HTML/SVG, document settings/CSS, bundle manifests/failure cleanup, media relinking and guarded Web pull |
| Web and portable paths | 38 offline mock API/path checks covering authentication, previews, conflicts, dependency guards, task states and platform/version paths |
| Portable public package | Five grouped offline checks passed; fresh public installation used matching packaged sources; downloaded release ZIP matched the checked archive |

I exercised structured writes only on synthetic in-memory data. Live editor and
workflow checks cancelled temporary dialogs. Menu discovery and options-dialog
checks do not certify a workflow's final output, and these checks do not
establish universal compatibility or a security audit.

## Compatibility and limitations

- The installer targets Gramps 6.0/6.1 with portable native paths. Gramps 6.0
  desktop execution and Linux/macOS GTK operation have not been verified.
- Third-party add-ons, custom widget types, online services and external report
  dependencies are not universally tested. Discovery or opening a dialog is not
  a guarantee that its final workflow will succeed.
- Existing add-on defects remain their own dependencies: the tested installation's
  Html View lacks its GTK HTML component. Static inspection found legacy PhpGedView
  and Rebuild Types constructor contracts incompatible with the current dispatcher.
  The plugin does not replace their implementations.
- No bundled Web server, automatic online synchronisation or automatic
  genealogical adjudication is provided. Live Web compatibility requires the
  target server's API and matching native Gramps release.
- Only one connected Gramps process per user discovery location is supported.
  Simultaneous instances can replace that locator.

## Check the package

```powershell
python .\verify_package.py
```

This checks packaging, configuration migration and the stdio protocol without
launching Gramps or reading a family tree. For developers, `verify_support.py`
tests native structured operations using a synthetic in-memory database inside
an already connected Gramps process; it requires trusted Python tool access.
`verify_menu_support.py` checks disposable native controls and read-only action
mapping. Run development checks with unsaved work closed; native runtime failures
can terminate the application.

`verify_integrations.py` runs offline mock Web/path checks. Its `--native` mode
and `verify_batch.py` require an isolated installed Gramps runtime and synthetic
profile/database; never use a live tree for those development checks.

## Troubleshooting and removal

If health reports `discovery_missing`, confirm the loader is installed and reopen
Gramps normally. For `bridge_unreachable`, inspect the actual Gramps process and
startup errors before launching again. For `version_mismatch`, save work and
reopen Gramps to load the matching bridge. For `dialogs_open`, finish or cancel
the observed dialog. Reconnect the client if tools are missing after installation.

To remove the integration, disable `gramps-desktop@gramps-desktop-plugins` in
the client's plugin settings and remove only its workspace configuration block.
For standalone mode, remove only `[mcp_servers.gramps_desktop]`. Save work and
close Gramps, then remove the add-on folder
`%APPDATA%\gramps\gramps61\plugins\DesktopMCPControl` after checking it belongs
to this plugin. Reopen Gramps normally. This does not remove your trees or media.

## Contributing and licence

I welcome reproducible installation reports and compatibility fixes. Open an
[issue](https://github.com/ufo8mycow14/gramps-desktop-plugin/issues) with the
platform, Gramps/Python/client versions, exact operation and a redacted error.
Use synthetic examples; exclude tree files, private screenshots, absolute personal
paths and connection tokens. Run `verify_package.py` before submitting changes.

The plugin is licensed under **GPL-2.0-or-later**, compatible with Gramps' licence.
See [LICENSE](LICENSE). Gramps and its dependencies retain their own copyrights
and licences. No Gramps runtime or family data is bundled.
