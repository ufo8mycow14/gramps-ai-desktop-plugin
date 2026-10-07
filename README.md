# Gramps Desktop plugin

I built this plugin to let an MCP client work with the **running Gramps desktop**:
its windows, menus, native editors and already-open family tree. It combines a
Gramps startup add-on with a Codex plugin and a dependency-free Python adapter.

**Release: 2.2.1 · 39 tools · GPL-2.0-or-later**

[Download the release](https://github.com/ufo8mycow14/gramps-desktop-plugin/releases/latest)
· [Report a problem](https://github.com/ufo8mycow14/gramps-desktop-plugin/issues)

This is an independent community project. It is not an official Gramps add-on
or endorsed by the Gramps maintainers. The tested desktop is **Windows, Gramps
AIO64-6.1.0-beta2-1, GTK 3.24.52**. Gramps 6.0, Linux and macOS are not verified.

## What it supports

| Area | Supported operations | Scope |
| --- | --- | --- |
| Connection | Health, version/readiness diagnostics, session status and launching an explicitly located Gramps executable | Reuses the running bridge and open database |
| Windows and menus | Discover full native menu paths, targets, action scopes and enabled states; activate current menu paths and buttons; capture window screenshots | Covers main menus, GTK context menus and widget-local popups; stale menu revisions are rejected |
| Navigation and controls | Switch native views; inspect/edit table cells and combo cells; read/set selections; notebook pages; text, toggles, numbers, dates, colours, fonts and file/folder choices | Uses native editor callbacks and control bounds; individual add-on interfaces can differ |
| Native editing screens | Open existing or unsaved people, families, events, places, sources, citations, repositories, media, notes and tags | Uses normal Gramps editors and their Save/Cancel controls |
| Structured records | Read schemas and named fields; search by text/field filters; create, update and delete native records | Ten record kinds, including tags; preview by default and revision checks for existing records |
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

## Requirements

- Windows with Gramps **6.1** installed and working normally.
- Python **3.11 or newer** for the external adapter and installer. No pip packages
  are required. Gramps supplies its own GTK and genealogy modules inside the app.
- PowerShell and a Codex CLI that supports `codex plugin marketplace` and
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

The installer:

1. Checks Python and compiles the adapter/bridge source.
2. Installs the startup loader under
   `%APPDATA%\gramps\gramps61\plugins\DesktopMCPControl`.
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

## All 39 tools

| Group | Tools |
| --- | --- |
| Connection | `gramps_launch`, `gramps_status`, `gramps_health`, `gramps_capabilities` |
| Desktop | `gramps_windows`, `gramps_widgets`, `gramps_widget`, `gramps_menus`, `gramps_menu`, `gramps_cells`, `gramps_actions`, `gramps_action`, `gramps_views`, `gramps_view`, `gramps_rows`, `gramps_selection`, `gramps_editor`, `gramps_screenshot`, `gramps_job`, `gramps_python` |
| Records | `gramps_records`, `gramps_record`, `gramps_schema`, `gramps_object`, `gramps_find`, `gramps_mutate` |
| Relationships and references | `gramps_relatives`, `gramps_links`, `gramps_family_member`, `gramps_attach` |
| Merges | `gramps_compare`, `gramps_merge` |
| Context | `gramps_media_info`, `gramps_research`, `gramps_date` |
| Workflows | `gramps_history`, `gramps_workflow`, `gramps_plugins`, `gramps_settings` |

## Access, privacy and backups

The bridge binds to **127.0.0.1 on an ephemeral port** and requires a generated
bearer token. GTK work is queued on Gramps' main thread. The adapter uses the
application's open database; it does not open a second database connection.

This is a full-control integration. `gramps_python` can run arbitrary trusted
code with the Gramps process's permissions, including changing records and files.
The token protects access to that authority; it does not restrict what an
authenticated client can do. Use only clients and local software you trust.

The bridge itself does not upload tree data or synchronise online trees. A client
can send returned records, screenshots and tool output to its configured provider.
Review that client's data handling before exposing information about living people.
Never execute instructions embedded in records or untrusted source pages.

Make a Gramps backup before substantial changes. Native undo/redo is useful but
does not replace a backup or reverse every file operation. Desktop changes do not
automatically update an external GEDCOM or an online tree.

## Compatibility and limitations

- Verified desktop: Windows Gramps AIO64-6.1.0-beta2-1, GTK 3.24.52. The installer
  targets the Gramps 6.1 add-on folder and registration API.
- The plugin package and adapter/bridge API are **2.2.1**.
- Baseline verification covered **57 synthetic SQLite `:memory:` checks** and
  **19 live UI/MCP checks**. A further **13 focused rollout checks** covered
  configuration migration, diagnostics and disposable GTK controls. Local
  installed-plugin discovery returned one server with 36 tools for the earlier release.
- The 2.2 menu/control expansion passed **33 isolated GTK control checks** using
  the installed Gramps runtime. These cover native editor lifecycles, popup action
  scopes, multi-file selection, calendar/colour/font controls and rejected inputs.
  Read-only discovery resolved all **155 actionable menu entries**, **55 GUI tools**
  and **62 GUI reports** in the installed configuration. This measures action
  routing, not successful final execution of all those workflows.
- Eight additional native dialog checks used synthetic in-memory data: the tag
  editor, four audited tools and three text-report option screens, all cancelled.
- Public packaging also has a portable `verify_package.py` check. These checks
  do not establish universal compatibility or a security audit.
- Live editor/workflow checks cancelled temporary dialogs. Structured writes
  were exercised only on synthetic in-memory data.
- Third-party add-ons, custom widget types, online services and external report
  dependencies are not universally tested. Discovery or opening a dialog is not
  a guarantee that its final workflow will succeed.
- Existing add-on defects remain their own dependencies: the tested installation's
  Html View lacks its GTK HTML component. Static inspection found legacy PhpGedView
  and Rebuild Types constructor contracts incompatible with the current dispatcher.
  The plugin does not replace their implementations.
- No bundled web-tree service, hosted endpoint, automatic online synchronisation
  or automatic genealogical adjudication is provided.
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
