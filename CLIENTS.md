# Client setup

I provide the same 58 Gramps tools to Codex, Claude Code, Kimi Code CLI,
Hermes Agent and OpenCode through a local stdio adapter. I support xAI/Grok
coding through OpenCode's xAI provider. The client and Gramps must run on the
same desktop with access to the same Python executable and files.

I checked the generated transports with real MCP initialise/list requests,
including launch from another working directory. I have not run these new
integrations inside the client applications. Their native schemas and commands
follow the primary documentation linked below; model availability and permission
prompts depend on the client and account.

## Install the Gramps bridge

Extract the release into a permanent directory. For clients other than Codex:

```powershell
.\install.ps1 -BridgeOnly -Python 'C:\path\to\python.exe'
```

Or, on a host with Python 3.11+:

```sh
python3 install.py --bridge-only --gramps-version 6.1
```

This installs the native Gramps loader and prepares `claude.mcp.json` with
the selected interpreter. It does not register a Codex plugin or change Codex
settings or its root `.mcp.json`. I preserve an installed explicit runtime
directory; a fresh installation records the host default as an absolute path.
`--runtime-dir` / `-RuntimeDirectory` overrides it for both adapter and loader.
I report the chosen directory in the installer result.

Keep the extracted directory in place. Save work and reopen Gramps normally
after installing or updating the bridge; allow startup dialogs to finish.
One connected Gramps process serves each discovery directory. All clients share
its open tree, history and session receipts; avoid concurrent writes from clients.

For a packaged Codex host and a different local CLI, their default AppData
directories can differ. The exporter reads the native loader's
`bridge_source.json` to reuse its explicit directory. It never reads the bearer
token in `connection.json`. Pass `--addon-dir` and `--gramps-version` when using
a custom native profile or Gramps 6.0; pass the same explicit `--runtime-dir`
to installation and export if the clients run in different host environments.
WSL, containers and remote clients need a working host bridge and path mapping;
Windows executable paths cannot be assumed to work there.

## Export a native configuration

Use the same Python executable as installation:

```powershell
python .\clients.py --client claude-code --output .\gramps-claude.json
python .\clients.py --client kimi --output .\gramps-kimi.json
python .\clients.py --client hermes --output .\gramps-hermes.yaml
python .\clients.py --client opencode --output .\gramps-opencode.json
python .\clients.py --client grok --output .\gramps-grok.json
```

Replace `python` with the absolute executable if needed. I export a snippet for
the requested client, using absolute interpreter, server and runtime paths.
I preserve existing output files unless `--overwrite` is explicitly supplied.
The exporter does not install clients, execute registration, change their settings,
select models or handle provider credentials. Merge only the `gramps_desktop`
entry into an existing configuration, preserving other servers and settings.

| Client | Configuration entry | Usual destination |
| --- | --- | --- |
| Claude Code | JSON `mcpServers.gramps_desktop`, `type: stdio` | Project `.mcp.json`; CLI `--scope user` registers in user settings |
| Kimi Code CLI | JSON `mcpServers.gramps_desktop` | `~/.kimi/mcp.json`, or the directory selected by `KIMI_SHARE_DIR` |
| Hermes Agent | YAML `mcp_servers.gramps_desktop` | Active profile's `config.yaml`; Windows default `%LOCALAPPDATA%\hermes`, POSIX/WSL default `~/.hermes`; respect `HERMES_HOME` |
| OpenCode / Grok | JSON `mcp.gramps_desktop`, `type: local`, executable array | Project `opencode.json`/`opencode.jsonc`, or `~/.config/opencode/opencode.json` |

## Claude Code native plugin

I include a Claude marketplace, native plugin manifest, shared workflow skill
and a Claude-specific transport file. After bridge-only installation, register
the same local directory so Claude caches the selected Python/runtime settings:

```text
claude plugin marketplace add /absolute/path/to/gramps-codex-desktop-plugin
claude plugin install gramps-codex-desktop-plugin@gramps-codex-desktop-plugins
```

On Windows, use the quoted absolute extracted-directory path. The public
marketplace can also be registered with:

```text
claude plugin marketplace add ufo8mycow14/gramps-codex-desktop-plugin
```

The public transport uses `python` on PATH and the default discovery location.
Use local installation or explicit standalone registration when the interpreter
or runtime differs. Claude reads root `.mcp.json` before the manifest-declared
`claude.mcp.json`; the later `gramps_desktop` entry replaces that same key.
The separate file supplies Claude's documented schema. Codex's additional root
transport fields have not been certified in Claude and can cause diagnostic logs.

Choose either native plugin installation or standalone MCP registration for
this server. Claude prefixes plugin tool names; use the discovered `gramps_*`
tools rather than assuming the Codex namespace. Use `claude mcp list` and the
session's `/mcp` view to inspect registration and connection.

## CLI registration and checks

I can print a shell-quoted registration command for each client:

```powershell
python .\clients.py --client claude-code --format command
python .\clients.py --client kimi --format command
python .\clients.py --client hermes --format command
```

PowerShell is the Windows default. Use `--shell posix` for a POSIX shell.
Run the printed command in that shell when choosing standalone registration.

- Claude uses `claude mcp add --transport stdio --scope user` followed by the
  server key, `--`, interpreter and adapter arguments.
- Kimi uses `kimi mcp add --transport stdio` followed by the server key, `--`
  and adapter command. It has no documented `--scope`. Check with
  `kimi mcp test gramps_desktop` and `/mcp`. `--mcp-config-file` can select an
  explicit file for a session. Existing skills can be added through
  `extra_skill_dirs = ["/absolute/plugin/skills"]` in Kimi configuration;
  `--skills-dir` replaces automatic skill discovery.
- Hermes uses `hermes mcp add gramps_desktop --command ... --args ...`.
  `--args` consumes the rest of the command; place any Hermes options before
  it. Registration probes the server and prompts for tool selection: select all
  tools for the complete surface. There is no unattended `--yes` option; a failed
  probe can save a disabled entry. Alternatively merge the generated YAML.
  Check with `hermes mcp test gramps_desktop`, then `/reload-mcp` in the session.
  Add the shared skill directory through `skills.external_dirs` in the active
  profile if desired. Hermes can modify writable external skill directories.
- OpenCode uses interactive `opencode mcp add`; I do not generate undocumented
  registration flags. Merge the generated JSON for deterministic setup and use
  `opencode mcp list` to inspect it. `opencode mcp auth` is for remote OAuth
  servers and is not needed by this local bridge.

Kimi and Hermes support here consists of MCP registration and optional shared
skills. Their native executable/portable plugin formats are separate contracts;
the Codex and Claude manifests are not native packages for those applications.

## xAI / Grok coding

I use OpenCode to connect a Grok model to the local Gramps tools:

1. Merge the generated `--client grok` entry into OpenCode configuration.
2. In OpenCode, run `/connect`, search for **xAI**, then use **SuperGrok
   Subscription** device-code authentication or **Manually enter API Key**.
3. Run `/models` and select an available Grok model of your choice.
4. Confirm `gramps_desktop` is connected, then request `gramps_health`,
   `gramps_status` and `gramps_capabilities` before editing.

I do not set a model or alter provider authentication. This route combines
OpenCode's documented xAI provider and local MCP support; successful Gramps tool
execution with a particular Grok model remains untested. xAI's hosted remote-MCP
API uses HTTP/SSE rather than local stdio. This package does not expose the
private desktop bridge publicly or send its bearer token to xAI.

## Connection and access

Use the exported discovery path when running the read-only health command:

```text
python /absolute/plugin/server.py --runtime-dir /absolute/runtime --call gramps_health
```

The adapter can initialise and list all 58 tools while Gramps is closed.
Actual desktop/database operations require a ready running bridge; health should
report `connected: true`, `version_match: true` and `ready: true`. Reconnect
clients after updates and reopen Gramps normally when its loaded bridge is older.
All clients get the same privileged local control and revision/preview/receipt
checks. Their own permission controls still apply. Follow the README's backup,
privacy and automatic lock recovery guidance before record writes.

## Primary references

- [Claude MCP](https://code.claude.com/docs/en/mcp),
  [plugin manifest](https://code.claude.com/docs/en/plugins/manifest-reference),
  [marketplace schema](https://code.claude.com/docs/en/plugins/marketplace-reference).
- [OpenCode local MCP](https://opencode.ai/docs/mcp-servers/),
  [configuration](https://opencode.ai/docs/config/),
  [xAI provider](https://opencode.ai/docs/providers/#xai).
- [Kimi MCP](https://github.com/moonshotai/kimi-cli/blob/main/docs/en/customization/mcp.md),
  [Kimi CLI reference](https://github.com/moonshotai/kimi-cli/blob/main/docs/en/reference/kimi-mcp.md).
- [Hermes MCP schema](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md),
  [registration implementation](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/subcommands/mcp.py).
- [xAI remote MCP](https://docs.x.ai/docs/guides/tools/remote-mcp-tools).
