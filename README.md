# lazy-anki-language-cards

lazy-anki-language-cards watches your clipboard. Each time you copy new text, it
takes a fullscreen screenshot and saves both to a JSON dataset. A local web page
(`127.0.0.1` only) shows every record. On that page you can select parts of a
sentence to annotate them, delete records, or undo the latest capture.

The Python package is called `lazy_anki_language_cards` (Python module names
cannot contain hyphens), so you run it with `python -m lazy_anki_language_cards`.

Linux first: fully supported on Ubuntu with X11 (Xorg), with Wayland support
where the compositor allows it (see [Linux display servers](#linux-display-servers-x11-vs-wayland)).

## Quick start

On Ubuntu or Debian with an X11 session:

1. **Install the system tools:**
   ```bash
   sudo apt install python3-venv xclip
   ```
2. **Go to the project folder:**
   ```bash
   cd /path/to/lazy-anki-language-cards
   ```
3. **Create a Python environment and install the dependencies:**
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
4. **Start the app:**
   ```bash
   .venv/bin/python -m lazy_anki_language_cards
   ```
   The page opens in your browser at `http://127.0.0.1:8765/`.
5. **Copy some text** in any application. It appears on the page with a
   screenshot.
6. **Stop the app** with the **Quit** button on the page or with **Ctrl+C** in
   the terminal.

On Wayland or another distribution, see [Installation](#installation) for the
right clipboard and screenshot tools.

## Supported operating systems

| Operating system / session | Status | Notes |
| --- | --- | --- |
| Ubuntu 24.04, X11 (Xorg) | ✅ Tested | Uses `xclip` and `mss` |
| Other Debian-based distros (Debian, Mint, Pop!_OS), X11 | 🟡 Will likely work | Same packages as Ubuntu |
| Other Linux distros (Fedora, Arch, openSUSE…), X11 | 🟡 Will likely work | Install `xclip` and Python 3.10+ with your package manager |
| Linux, KDE Plasma on Wayland | 🟡 Will likely work | Needs `wl-clipboard` and `spectacle` |
| Linux, Sway / Hyprland / other wlroots on Wayland | 🟡 Will likely work | Needs `wl-clipboard` and `grim` |
| Linux, GNOME on Wayland (Ubuntu default) | 🟡 Will likely work | Clipboard works; screenshots may fail on recent GNOME. Use "Ubuntu on Xorg" if they do |
| macOS | ❌ Won't work | No supported clipboard tool (no `xclip`/`wl-paste`) |
| Windows | ❌ Won't work | No supported clipboard tool and no X11/Wayland session |
| Windows with WSL2 / WSLg | ❌ Won't work | Screenshots only see Linux windows, not the Windows desktop |

## Tech stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.10+ (tested with 3.12) |
| Web server and JSON API | [Flask](https://flask.palletsprojects.com/) 3.x (Werkzeug threaded dev server, Jinja2 templates) |
| Live updates | Server-Sent Events (SSE) with an in-process pub/sub bus |
| Frontend | Plain HTML, CSS and vanilla JavaScript (no framework, no build step, no CDN) |
| Screenshots (X11) | [mss](https://github.com/BoboTiG/python-mss) 9.x, with `scrot` or ImageMagick `import` as fallbacks |
| Screenshots (Wayland) | `gnome-screenshot` (GNOME), `spectacle` (KDE), `grim` (wlroots) |
| Clipboard | `xclip` (X11, GNOME Wayland via Xwayland), `wl-paste` from `wl-clipboard` (other Wayland) |
| Storage | A single UTF-8 JSON file with atomic writes (temp file, fsync, rename) and PNG screenshots |
| Concurrency | Python `threading` (clipboard polling thread and threaded web server sharing one lock) |
| CLI | Python `argparse` |
| Tests | [pytest](https://pytest.org/) 8.x |
| Platform | Linux desktop with X11 or Wayland |

## Installation

Requirements: Python 3.10+, a desktop session, and a clipboard tool.

```bash
# System tools (Ubuntu/Debian)
sudo apt install python3-venv xclip          # X11 (and GNOME on Wayland)
# sudo apt install wl-clipboard grim         # Sway/Hyprland and other wlroots compositors
# sudo apt install wl-clipboard kde-spectacle  # KDE Plasma on Wayland

# Python environment
cd /path/to/lazy-anki-language-cards
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python dependencies: **Flask** (web server and API) and **mss** (fast X11
screenshots). Everything else comes from the standard library.

## Running

```bash
.venv/bin/python -m lazy_anki_language_cards
```

The terminal prints the URL, the data file and the screenshots folder, and the
page opens in your default browser. lazy-anki-language-cards listens on
`http://127.0.0.1:8765/`. If that port is busy, it picks a free one and prints
which.

### Desktop launcher (.desktop file)

To start the app from your application menu or desktop instead of a terminal,
run this from the project folder. It writes an executable
`lazy-anki-language-cards.desktop` file that points to this folder's `.venv`:

```bash
DESKTOP_FILE=~/.local/share/applications/lazy-anki-language-cards.desktop
mkdir -p ~/.local/share/applications
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=lazy-anki-language-cards
Comment=Capture copied text with a screenshot and annotate it
Exec="$PWD/.venv/bin/python" -m lazy_anki_language_cards
Path=$PWD
Icon=accessories-text-editor
Terminal=false
Categories=Utility;
EOF
chmod +x "$DESKTOP_FILE"
```

The app then shows up in your application menu as **lazy-anki-language-cards**.
To also put an icon on your desktop:

```bash
cp "$DESKTOP_FILE" ~/Desktop/
gio set ~/Desktop/lazy-anki-language-cards.desktop metadata::trusted true  # GNOME: allow launching
```

Notes:

- The file stores absolute paths. Run the command again if you move the
  project folder or recreate `.venv`.
- With `Terminal=false` there is no terminal window, so stop the app with the
  **Quit** button on the page. Change it to `Terminal=true` to see the log and
  use Ctrl+C.
- To add options, append them to the `Exec=` line, e.g.
  `Exec="…/.venv/bin/python" -m lazy_anki_language_cards --port 8765`.
- To remove the launcher, delete the `.desktop` file(s).

Useful options (`.venv/bin/python -m lazy_anki_language_cards --help` lists them all):

| Option | Purpose |
| --- | --- |
| `--data-dir PATH` | Store data somewhere else (default below) |
| `--port N` | Use a fixed port (fails with a clear message if it is busy) |
| `--no-browser` | Don't open the page automatically |
| `--interval SECONDS` | Clipboard polling interval (default `0.3`) |
| `--clipboard-backend {auto,xclip,wl-paste}` | Force a clipboard tool |
| `--screenshot-backend {auto,mss,scrot,import,gnome-screenshot,grim,spectacle}` | Force a screenshot tool |
| `-v` | Debug logging, including HTTP requests |

### Stopping

Both ways of quitting ask whether to **keep or discard `records.json`**:

- **Quit** button in the page: a dialog offers **Keep file & quit**, **Discard
  file & quit** or **Cancel**. **Copy JSON first** copies the dataset before you
  decide. A checkbox also deletes the screenshots when discarding; it is off by
  default.
- **Ctrl+C** in the terminal: lazy-anki-language-cards stops capturing, then asks
  `Keep …/records.json (N records)? [K]eep / [d]iscard`. Pressing Enter
  keeps the file. If you discard, it also asks whether to delete the screenshots
  (default: no).

"Discard" deletes `records.json`; the next start creates a fresh, empty one.
Screenshot deletion only removes images lazy-anki-language-cards created. The file is always
**kept** when the app is stopped without a terminal to ask in (e.g. `kill`,
SIGTERM, no TTY), or if you press Ctrl+C again at the prompt.

## Using the page

- **New captures appear automatically** at the top. The newest record is outlined.
  Updates arrive by Server-Sent Events and need no reload.
- **Annotate:** select text inside a sentence with the mouse or Shift+arrows
  and press **`A`**. An **Annotate "…"** button also appears under the
  selection; clicking it or pressing **Enter** does the same, and **Esc**
  cancels.
  - With nothing selected, **`A`** annotates the **word under the text cursor**.
    Click inside a word, then press `A`. Word boundaries are Unicode-aware. The exact substring is stored, with case,
  punctuation and Unicode intact. Whitespace at the edges of the selection is
  trimmed.
  - Annotated text is highlighted in the sentence and listed as chips below it.
    Click **×** on a chip to remove that annotation.
  - Selecting text that is already annotated shows **Already annotated**;
    duplicates are never stored.
  - Text that appears several times: the selected text is stored once.
    Every occurrence of it is then highlighted.
- **Delete** removes a record from the dataset. Its screenshot file stays on disk.
- **Undo latest: `Ctrl+Z`** removes the most recently *added* record. That
  means the newest capture still in the dataset, whatever you deleted before.
  Pressing it again removes the one before that. It is ignored while typing in a
  text field, and holding the keys down does not remove several records. The
  **Undo latest** button does the same thing.
- **JSON tab** shows `records.json` exactly as it is on disk and updates live.
  **Copy JSON** puts the whole file on the clipboard. You can also select part
  of it and press Ctrl+C. **Open raw file** opens it at `/records.json`.
- **Copies made from the lazy-anki-language-cards page are never recorded.** This covers
  Copy JSON and Ctrl+C on anything in the page. Before the text reaches the
  clipboard, the page tells the monitor to ignore it.
- **Pause capture** ignores copies until you untick it. Text copied while paused
  is *not* recorded later. Use it before copying anything sensitive.
  Clipboard contents marked as secrets by password managers (KeePassXC, KDE)
  are always skipped.

Text already on the clipboard when lazy-anki-language-cards starts is not recorded. Copying
the same text again while it is still on the clipboard does not create a second
record. Empty or whitespace-only text and non-text clipboard contents (images,
files) are ignored.

### Keyboard shortcuts (on the page)

| Key | Action |
| --- | --- |
| `A` | Annotate the selected text, or the word under the cursor |
| `Enter` | Annotate the selection shown in the Annotate button |
| `Esc` | Cancel the current selection / close the quit dialog |
| `Ctrl+Z` | Remove the most recently added record |

Shortcuts are ignored while typing in a text field and while the quit dialog is
open. `A` works only in the Records tab.

## Where the data lives

Default data directory: `~/.local/share/lazy-anki-language-cards/` (it follows
`$XDG_DATA_HOME`; override with `--data-dir` or `LAZY_ANKI_LANGUAGE_CARDS_DATA_DIR`).

```
~/.local/share/lazy-anki-language-cards/
├── records.json          # the dataset
└── screenshots/
    ├── 2026-10-24_005300_001.png
    └── ...
```

The page footer and the startup log also show both paths.

`records.json` is a UTF-8, indented JSON array in insertion order (oldest
first):

```json
[
  {
    "sentence": "The copied text goes here.",
    "image_path": "screenshots/2026-10-24_005300_001.png",
    "words": ["copied text", "another substring"]
  }
]
```

- `image_path` is relative to the data directory, so it stays valid if you move
  the whole folder. If a screenshot fails, the record is still saved with
  `"image_path": null`, and the page shows the error.
- Each change is written immediately with an atomic replace: write a temp file,
  fsync, then rename. A crash leaves either the old or the new file, never a
  half-written one.
- If `records.json` is not valid JSON, lazy-anki-language-cards refuses to start and leaves it
  untouched. You can then fix it or move it away.
- You can edit the file by hand while lazy-anki-language-cards is stopped.

## Linux display servers (X11 vs Wayland)

lazy-anki-language-cards polls the clipboard with a command-line tool and takes screenshots
with a library or tool. All display-server specific code is isolated in
`lazy_anki_language_cards/platform_env.py`, `clipboard.py` and `screenshot.py`.

**X11 / Xorg (fully supported).** Any X11 program may read the clipboard and the
screen. lazy-anki-language-cards uses `xclip` and `mss` (falling back to `scrot` or
ImageMagick `import` if installed). On Ubuntu, choose **"Ubuntu on Xorg"** on
the login screen (gear icon) to get this mode.

**Wayland (depends on the compositor).** Wayland deliberately stops
applications from reading the clipboard or the screen in the background:

| Desktop | Clipboard | Fullscreen screenshot |
| --- | --- | --- |
| GNOME (Ubuntu default) | `xclip` through Xwayland (GNOME mirrors the clipboard to Xwayland). `wl-paste` is avoided because GNOME lacks the data-control protocol, so it would steal focus on every poll | `gnome-screenshot`, **if** your GNOME version still lets it capture. Recent GNOME releases restrict screenshots to the shell itself. If captures fail, use "Ubuntu on Xorg" |
| KDE Plasma | `wl-paste` | `spectacle` |
| Sway, Hyprland, other wlroots | `wl-paste` | `grim` |

X11 grabbers (`mss`, `scrot`, `import`) are not used on Wayland: they would
only see Xwayland windows, or a black screen. If no working screenshot tool is
found, lazy-anki-language-cards refuses to start and says what to install.

**Timing.** The clipboard is polled every 0.3 s (`--interval`). The screenshot
is taken as soon as a change is detected and before the record is saved. It
therefore shows the screen at most one polling interval after the copy.

## Troubleshooting

| Message / symptom | Fix |
| --- | --- |
| `missing Python dependency` | `.venv/bin/pip install -r requirements.txt`, and run with `.venv/bin/python` |
| `No graphical desktop session detected` | Run from a terminal inside your desktop session. From SSH/systemd, export `DISPLAY=:0` (X11) or `WAYLAND_DISPLAY`/`XDG_SESSION_TYPE=wayland` |
| `No clipboard tool found` | `sudo apt install xclip` (X11, GNOME) or `sudo apt install wl-clipboard` |
| `Cannot read the clipboard with xclip: Can't open display` | `DISPLAY` is wrong or not exported; check `echo $DISPLAY` in a desktop terminal |
| `No screenshot tool available` | `.venv/bin/pip install mss` (X11), or install the tool for your Wayland desktop (table above) |
| Records appear but say "No screenshot" | The page shows the screenshot error. On GNOME Wayland, switch to "Ubuntu on Xorg" |
| `Cannot listen on 127.0.0.1:N` | That port is in use. Choose another `--port`, or omit `--port` to pick a free one |
| `records.json is not valid JSON` | Repair the file, or move it away to start a fresh dataset |
| Page says "Reconnecting…" | The app is not running (or restarting); the page reconnects by itself once it is back |
| Copy JSON says "Copy failed" | The browser refused clipboard access (rare on `127.0.0.1`). Select the text in the JSON tab and press Ctrl+C |

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The tests cover storage (schema, ordering, persistence, atomic writes,
concurrent writers, undo semantics), the HTTP API (annotate, delete, undo,
pause, shutdown, request guards, SSE), the clipboard monitor (deduplication,
ignored content, screenshot-before-save, failures), clipboard parsing,
screenshot file naming and port handling.

### Project layout

```
.
├── README.md
├── requirements.txt          # runtime: Flask, mss
├── requirements-dev.txt      # + pytest
├── lazy_anki_language_cards/
│   ├── __init__.py
│   ├── __main__.py           # `python -m lazy_anki_language_cards`, dependency check
│   ├── app.py                # CLI, startup checks, lifecycle, keep/discard on quit
│   ├── platform_env.py       # X11 / Wayland / desktop detection
│   ├── clipboard.py          # xclip and wl-paste backends
│   ├── screenshot.py         # mss and command-line screenshot backends, file naming
│   ├── monitor.py            # background clipboard polling thread
│   ├── storage.py            # JSON dataset, locking, atomic writes
│   ├── events.py             # pub/sub feeding Server-Sent Events
│   ├── web.py                # Flask routes and JSON API
│   ├── templates/index.html
│   └── static/
│       ├── app.css
│       └── app.js            # rendering, annotation, shortcuts, JSON view, quit dialog, live updates
└── tests/
    ├── conftest.py
    ├── test_app.py
    ├── test_api.py
    ├── test_monitor.py
    └── test_storage.py
```

### Design notes

- The clipboard monitor runs in its own thread and never waits on HTTP requests.
  The web server is threaded. One lock in `RecordStore` serializes every change
  from both sides. Each change writes the file first and updates memory only if
  the write succeeds.
- Records get an in-memory `id` for the API. It is never written to disk, so
  the JSON keeps exactly the three documented fields.
- The page maps a selection to character offsets in the original sentence. It
  stores `sentence.slice(start, end)`, so the stored text comes from the source
  string, not from browser-normalized selection text. The server checks again
  that the word is a substring.
- The server only accepts requests whose `Host` is `127.0.0.1:<port>` or
  `localhost:<port>`, which blocks DNS rebinding. Changes require an
  `X-Lazy-Anki-Language-Cards` header, so other websites cannot drive the API from your
  browser.
