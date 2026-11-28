# PeakCut

Turn long videos and live streams into captioned vertical clips, ready to post. Free and open source: run it on
your own computer or server, with your own AI key.

Paste a YouTube, Twitch or Kick link (or upload a file) and PeakCut finds the moments worth clipping, cuts them to
9:16, adds word-by-word captions and a hook, and can post them to your connected accounts. It can also record a
creator's live stream and clip the highlights while the stream is still running.

## What it does

- **Create video.** Start from a topic, confirm the channel's niche, research at least 20 references,
  write a documented script, synthesize narration, edit real media into a 9:16 pilot, revise it and export
  the final video. Each operation rereads the instructions in `criacao_de_videos/` and its required project
  documents. See [the creation guide](docs/criacao-de-videos.md) for service setup and review controls.

- **Clips from any video.** Transcribes the audio, asks an AI which moments would hold a stranger's attention, and
  renders each one with captions in one of three styles.
- **Live clipping.** Records a stream into a rolling buffer and clips as it happens, using loudness, chat activity
  and the transcript.
- **Workspaces per creator.** Channels, clip settings, posting rules and an activity log for each creator you clip.
- **Posting.** Connect YouTube, Instagram and TikTok in one click, then queue or auto-post finished clips, with
  spacing and optional daily caps, off by default (see [DEPLOY.md](DEPLOY.md#connecting-youtube-instagram-and-tiktok) for the one-time setup).
- **No sign-in on your own computer.** It opens straight into the dashboard. On a shared server, switch accounts on
  (`PEAKCUT_ACCOUNTS=1`) for email or Google sign-in and an admin dashboard.
- **Your own AI.** Gemini, OpenAI, a Codex subscription, a Claude subscription, Grok, Groq, OpenRouter, Claude, a local model through Ollama, or anything that
  speaks the OpenAI chat format. No key at all also works: clips are then picked by loudness.

## Quick start

You need Python 3.10 or newer and `ffmpeg`. On Windows: `winget install Gyan.FFmpeg`. On a Mac: `brew install ffmpeg`.
On Linux: `sudo apt install ffmpeg`. PeakCut finds it by itself and tells you in the dashboard if it is missing.

Put PeakCut in a short folder that stays put: `C:\peakcut` on Windows, `~/peakcut` on a Mac or Linux. Not inside a
temporary folder or an AI assistant's workspace: those get cleaned up, and on Windows a folder that deep stops the
speech model from loading (Windows can't open files whose full path is over 260 characters). If an AI assistant
installs it for you, it should ask you where to put it first ([AGENTS.md](AGENTS.md) tells it to).

On Windows:

```bash
git clone <this repository> C:\peakcut
```

```bash
cd C:\peakcut
```

On a Mac or Linux:

```bash
git clone <this repository> ~/peakcut
```

```bash
cd ~/peakcut
```

```bash
pip install -r requirements.txt
```

Start the server:

```bash
python -m studio.server
```

The first start downloads the speech model by itself (about 480 MB, once).

Open http://127.0.0.1:8765. There is no sign-in: it opens straight into the dashboard. Go to **Settings, AI**, choose
your AI service, paste your key and press **Save and test** (see [Choose your AI](#choose-your-ai)).

## Choose your AI

PeakCut needs a chat model that can return JSON. The easy way is in the dashboard: **Settings, AI**. Pick the
service, paste your key, press **Find models** to choose from the service's own list, then **Save and test**. The
key is kept in the data folder on your machine and is never shown again.

If you prefer a file (or run a server), the same three things can go in `.env`. What is saved in the dashboard wins:

```ini
PEAKCUT_AI_PROVIDER=gemini
PEAKCUT_AI_MODELS=gemini-2.5-flash
PEAKCUT_AI_KEY=your-key-here
```

| `PEAKCUT_AI_PROVIDER` | Service | Where to get a key |
|---|---|---|
| `gemini` | Google Gemini | https://aistudio.google.com/apikey |
| `openai` | OpenAI | https://platform.openai.com/api-keys |
| `codex` | Codex (your ChatGPT subscription) | Sign in through Codex CLI; see below |
| `claude_sub` | Claude (your Pro or Max subscription) | Sign in through Claude Code; see below |
| `xai` | Grok (xAI) | https://console.x.ai |
| `groq` | Groq | https://console.groq.com/keys |
| `openrouter` | OpenRouter (many models, one key) | https://openrouter.ai/keys |
| `anthropic` | Claude | https://console.anthropic.com |
| `ollama` | A model on your own machine, no key | https://ollama.com |
| `custom` | Any OpenAI-compatible address: also set `PEAKCUT_AI_BASE_URL` | |

- `PEAKCUT_AI_MODELS` is the model id exactly as the service names it. Model names change often, so check the
  service's current list. Several ids separated by commas are tried in order, which gives you a fallback.
- A fast, inexpensive model is the right choice. A one-hour video is read in about four requests.
- You pay the AI service directly for what you use. The **AI usage** page in the app shows how many requests and
  tokens each video took.
- Speech to text does not use the AI service. It runs on your own machine, so audio never leaves it.

### Use your Codex subscription

Install [Codex CLI](https://developers.openai.com/codex/cli) or the Codex desktop app on the computer that runs
PeakCut. Use a recent CLI supporting `--ignore-user-config`, `--ignore-rules` and app-server account login.

Open **Settings, AI subscription**. If Codex is already signed in, the page shows your account and plan. Otherwise,
press **Connect ChatGPT**, finish sign-in on the official ChatGPT page, and return to PeakCut. It checks the login
automatically. Choose an available model from the dropdown (or **Automático** to let Codex choose) and a supported
reasoning effort. The effort list updates with the selected model: Baixo, Médio, Alto, Extra alto and any other levels
that model offers. Keep **Padrão do modelo** for its default effort. Press **Usar esta assinatura**, **Salvar e testar**
or **Testar assinatura**. Your choice is saved only after a successful test and is used for text and image requests. No API key
or password is entered into PeakCut. Calls use your subscription's Codex allowance and limits.
PeakCut refuses an API-key login for this provider and never falls back to API billing.

The account is shared with Codex on this computer. **Connect another account** opens the official login again.
**Cancel connection** only cancels the pending attempt; it does not sign Codex out. The login link expires after ten
minutes. For terminal-only setups, `codex login` still works. The original **Settings, AI** route remains supported.

The adapter is Python and invokes the official CLI, which handles login and credential renewal. It runs each
request separately in a temporary, read-only working directory, with shell execution, web search, apps, plugins,
image generation and additional agents disabled. Text, JSON clip suggestions,
captions and image analysis use the same subscription. Calls run one at a time; live clipping may need a faster model
to meet its short deadlines. On a different machine or in a container, Codex must be installed and signed in there.

To configure it through `.env` instead of the dashboard:

```ini
PEAKCUT_AI_PROVIDER=codex
PEAKCUT_AI_MODELS=default
```

If Codex cannot be found, set `PEAKCUT_CODEX_COMMAND` to the executable's full path. On Windows the adapter also
queries Windows' registered desktop application when its executable is absent from PATH, and finds the native
binary inside standard npm installations.

### Use your Claude subscription

Install [Claude Code](https://code.claude.com/docs/en/setup) on the computer that runs PeakCut. Your Claude Pro or
Max plan is then used for clip selection, titles, captions and pictures, with no API key.

Open **Settings, AI subscription** and press **Connect Claude**. Claude Code opens the official Claude sign-in page.
On the same computer, sign in and PeakCut notices by itself. From another device, press **Continue sign-in**, sign in,
and paste the code that page shows into PeakCut. Then choose a model (Sonnet is fast and good for clips, or
**Automatic** for your plan's usual model) and a reasoning effort, and press **Use this subscription**. The choice is
saved only after a successful test. For terminal-only setups, `claude auth login` works too.

PeakCut runs `claude -p` for each request, in an empty temporary folder, with every tool, MCP server, setting file
and memory file switched off, and without saving the session. API keys in the environment are removed for these
calls, so they always use the plan and never API billing; an API-key login is refused. Calls run one at a time and
count toward your plan's usage limits; live clipping may need a faster model such as Sonnet or Haiku.

To configure it through `.env` instead of the dashboard:

```ini
PEAKCUT_AI_PROVIDER=claude_sub
PEAKCUT_AI_MODELS=sonnet
```

If Claude Code cannot be found, set `PEAKCUT_CLAUDE_COMMAND` to the executable's full path. Standard npm
installations and the native installer (`~/.local/bin`) are found automatically.

### Videos in Arabic

PeakCut hears English by default. To clip videos in Arabic, add the Arabic speech model once in
**Settings → Speech models** (about 107 MB), or choose **Arabic** under Options when adding a video and PeakCut
offers to download it. Set a creator's language on their Setup page so their live recordings use it too.
It understands Modern Standard Arabic and everyday dialects, and the AI turns English words said in between
back into English. Captions on the clips are in English.

### YouTube automatic translation

Under **Settings → Language**, **Use YouTube's automatically translated English captions** is on by default.
PeakCut prefers the original transcript. If it is unavailable, this switch allows YouTube's English translation;
it also controls automatically translated tracks when preparing English captions. Turn it off to use original
transcripts, human-written subtitles and your configured AI for caption translation instead. Each account saves
its own choice, and new videos and live recordings keep the choice made when they were created.

The spoken language is chosen separately when adding a video or in a creator's Setup. Portuguese uses the
multilingual speech model; the output caption language still controls titles, descriptions and captions.

## Settings

Everything is set through environment variables, normally in a `.env` file next to this README.
[`.env.example`](.env.example) lists every setting with a short explanation. The ones that matter first:

| Setting | What it is for |
|---|---|
| `PEAKCUT_AI_PROVIDER`, `PEAKCUT_AI_MODELS`, `PEAKCUT_AI_KEY` | The AI that picks clips and writes titles (or set it in the dashboard) |
| `PEAKCUT_ACCOUNTS` | `1` makes everyone sign in. Use it on any server other people can reach |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional: Sign in with Google, and connecting YouTube |
| `PEAKCUT_SMTP_*`, `PEAKCUT_MAIL_FROM` | Optional: confirmation and password emails |
| `PEAKCUT_DATA` | Where videos, clips and the database are kept (default `./data`) |

`.env`, the `secrets/` folder and all user data are ignored by git. Keep keys there, never in the code.

## Admins

This only matters with accounts switched on (`PEAKCUT_ACCOUNTS=1`); without them you are the owner and can do
everything. An account is an admin only when the database says so. No email address is special in the code and no web request
can change a role. Do it on the machine that runs PeakCut:

```bash
python -m studio.admin grant you@example.com
```

`revoke` and `list` work the same way. The admin dashboard is at `/admin` and answers "not found" to everyone else.

## How it is built

| Part | What it is |
|---|---|
| `studio/` | The server: FastAPI, the clip pipeline, live recording, accounts |
| `studio/ai.py` | The only file that talks to an AI service. Add a provider here |
| `web/` | The pages: landing, sign-in, the app, the admin dashboard (plain HTML, CSS and JavaScript, no build step) |
| `tests/` | Tests that run against a throwaway copy of the app |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | One-server deployment with PostgreSQL and automatic HTTPS |

Video work is done with `ffmpeg` and `yt-dlp`. Speech is transcribed with `sherpa-onnx` and NVIDIA's Parakeet
model (English) and FastConformer Arabic model, both released by NVIDIA under the Creative Commons Attribution 4.0
licence (CC-BY-4.0). The Arabic model was converted for sherpa-onnx with `tools/export_arabic_model.py`.
Data is kept per user: accounts in the database (SQLite on your own computer, PostgreSQL on a server), videos and
clips in each user's own folder. [AGENTS.md](AGENTS.md) is a short map of the code for contributors and AI coding
assistants.

## Where your videos and clips are kept

Everything PeakCut keeps is in one folder: videos, clips, recordings, creators, settings and the speech model. By
default it is the `data` folder inside PeakCut. **Settings, Storage** shows the folder and how much room its drive
has, opens it in one click, and lets you move it to another drive: choose the new folder, and PeakCut moves
everything there the next time it starts. If the drive is nearly full, PeakCut says so and stops taking new videos
until there is room. (`PEAKCUT_DATA` in `.env` still works and takes priority.)

## Updating

PeakCut checks GitHub once a minute while it runs. When a newer version is out, the dashboard shows a notice
within a minute or two (no restart needed), with what changed
and how to update: ask your AI coding assistant to do it (the notice has a sentence to paste), or run
`git pull && pip install -r requirements.txt` in the PeakCut folder and restart it (with Docker:
`git pull && docker compose up -d --build`). Your `.env`, keys and clips are not touched. Only the two commit ids
are sent to GitHub; set `PEAKCUT_UPDATE_CHECK=0` to turn the check off.

## Deploy

[DEPLOY.md](DEPLOY.md) walks through a single server with Docker: the app, PostgreSQL, and Caddy for HTTPS.

```bash
docker compose up -d --build
```

## How it behaves under load

- The page asks the server one small question every few seconds. When nothing has changed, the answer comes
  straight from memory.
- Each person's videos wait in their own line and the lines take turns, so one person cannot hold everyone up.
- The server measures its own processor, memory and disk every second. Extra videos and recordings start only
  while there is room, wait briefly when it is nearly full, and continue the moment it frees up.
- There are no limits on upload size, video length, recordings, creators or storage unless you set them in
  `.env`. Five videos can be sent at once. Request sizes are always capped, so junk can't fill the memory.

## Tests

```bash
python tests/run.py
```

This starts a copy of the app on a spare port with an empty temporary data folder, runs the permission, abuse and
queue tests against it, then removes it. It needs no keys and never touches real data.

## Security

- With sign-in off, PeakCut listens on this computer only (`127.0.0.1`), refuses changes sent by other websites,
  and only answers to its own address. Do not expose it to the internet that way: turn accounts on.
- Passwords are stored only as salted scrypt hashes. Sessions are random tokens stored hashed; cookies are HTTP-only.
- Every route works out who is asking from the session, never from the request, so users only reach their own data.
- Only known video sites are fetched, and text from videos is treated as data when it is shown to the AI.
- Found a problem? Please report it privately through the repository's security advisories instead of opening a
  public issue.

## Using it responsibly

Clip videos you made, or have permission to use. You are responsible for the rights to what you download, clip and
post, and for following the terms of the sites you fetch from and post to.

## Licence

[MIT](LICENSE). Caption fonts in `studio/fonts/` (Anton, Archivo Black, Poppins) are under the SIL Open Font
License; the licence texts are in the same folder.
