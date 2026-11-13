# Connect an AI app (MCP)

Ember & Tide speaks the [Model Context Protocol](https://modelcontextprotocol.io), so the AI app you already use can look at
your sleep and your bed: Claude Desktop, Claude Code, VS Code with GitHub Copilot (Agent mode), Cursor, or any other app that
supports MCP. It is another way in besides the built-in Ask page; the server answers on the same port as the app, at
`https://<your-server>/mcp`.

Try things like "How did I sleep last night?", "Review my week", "I'm too warm, can you cool my side a little?"

## What the AI app can see and do

- A key belongs to **one person** and acts as that person only: their status, nights, rhythm, Autopilot suggestions and morning
  review. It never sees the other person's private data.
- A **look only** key can read. A **look and change** key can also ask for changes, using exactly the same actions, limits and
  safety checks as Ask (warmer or cooler tonight, a flash, a rhythm tweak of at most 3 degrees, Autopilot mode, notifications).
  Small, reversible changes apply right away only if that person turned on "small changes" in Settings; everything else becomes
  a card the person confirms with Apply in the app. Changes show in the same history as Ask's, marked as coming from the AI app
  (with the key's label).
- Your AI app also asks you to approve tool calls itself. The server's limits apply either way.

## Privacy

What an AI app reads is sent to the company that runs it (Anthropic, GitHub, OpenAI, and so on) so the model can answer you.
Sleep data is personal: only connect apps whose provider you are comfortable with, and prefer a look only key. Nothing leaves
your server unless you connect an app. Text from your household (names, notes) is passed on as plain data with control
characters removed and a length cap, and the server tells the model not to follow instructions inside it.

## Set up

1. In Studio open **Connect an AI app**, give the key a label ("Claude Desktop on laptop") and pick look only or look and
   change. The key is shown once; copy it. Only a hash is kept on the server.
2. Paste the matching setup into your AI app. Studio fills in your address and key. Replace `<server>` with the address your AI
   app can reach (the public address if the app runs away from home).

**Claude Code**

```
claude mcp add --transport http embertide https://<server>/mcp --header "Authorization: Bearer <key>"
```

**Claude Desktop** (Settings, Developer, Edit config; needs Node.js). It connects through `mcp-remote`:

```json
{
  "mcpServers": {
    "embertide": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<server>/mcp", "--header", "Authorization:${EMBERTIDE_AUTH}"],
      "env": { "EMBERTIDE_AUTH": "Bearer <key>" }
    }
  }
}
```

**VS Code with Copilot** (`.vscode/mcp.json`, then use Copilot Chat in Agent mode):

```json
{
  "servers": {
    "embertide": { "type": "http", "url": "https://<server>/mcp", "headers": { "Authorization": "Bearer <key>" } }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`):

```json
{ "mcpServers": { "embertide": { "url": "https://<server>/mcp", "headers": { "Authorization": "Bearer <key>" } } } }
```

**Anything else.** Use the URL and the header `Authorization: Bearer <key>`. An app that only speaks stdio can use
`npx -y mcp-remote https://<server>/mcp --header "Authorization:${EMBERTIDE_AUTH}"` with `EMBERTIDE_AUTH` set to `Bearer <key>`.

## Tools

Read (any key):

| Tool | What it gives |
| --- | --- |
| `get_status` | Bed online, in bed or not, current phase, target against pad temperature, tonight's changes and plan |
| `get_last_night` | One night's summary, story and timeline (default: the night that just ended) |
| `get_nights` | Summaries for a date range, at most 31 nights |
| `get_rhythm` | Phases with times and temperatures (the ids the change tools need) |
| `get_suggestions` | Autopilot's open suggestions |
| `get_ai_review` | The written morning review of a night, if there is one |

Temperatures come as `c`, `f` and `shown` (in that person's unit).

Change (look and change keys only; the same actions as Ask): `adjust_tonight`, `change_rhythm_phase`, `start_flash`,
`stop_flash`, `turn_off_tonight`, `set_autopilot_mode`, `handle_suggestion`, `mark_night_stretch`, `set_notifications`.
Each key may make at most 30 change calls an hour, and the assistant's own limits (10 applied changes an hour, 1.5 degrees of
automatic change a night) apply too.

Prompts: `review_my_week`, `how_was_last_night`, `make_it_comfier`.

## Keys

Each person makes their own keys in Studio, **Connect an AI app** (look only or look and change). Keys list their last use and
can be revoked there at any time; the owner can revoke anyone's in **People & access**. A key stops working if its account is
removed or moved to another side. A key only works for `/mcp`, never for the rest of the API. With `ET_AUTH=off` no key is
needed and the tools act as the first person (or `/mcp?person=<id>`).

## Troubleshooting

- **401 Unauthorized.** The key is missing, mistyped, revoked, or the `Authorization: Bearer <key>` header never reached the
  server. Make a new key.
- **429.** Too many wrong keys from one address; wait a few minutes.
- **Works at home but not away.** The AI app has to reach the server: use the public HTTPS address, not a home address.
- **Behind a reverse proxy.** It must pass the `Authorization` header and must not buffer responses. For nginx:
  `proxy_set_header Authorization $http_authorization; proxy_buffering off;`. Cloudflare Tunnel and Cloudflare proxying work as
  they are.
- **The AI app says "read-only".** The key is look only; make a look and change key.
- **A change did not happen.** It probably became a card: open the app and tap Apply, or turn on small changes in Settings for
  small tweaks. Cards expire after 30 minutes.
