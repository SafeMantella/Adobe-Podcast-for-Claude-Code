# Adobe Podcast noise removal

This repository removes noise from video and audio with Adobe Podcast Enhance Speech. It works with any coding agent. `CLAUDE.md` and `GEMINI.md` point here.

When the user asks to remove noise, clean audio, enhance speech, run Adobe Podcast, tirar ruído, or limpar áudio, read `skills/adobe-podcast-enhance/SKILL.md` and follow it. Do not invent a different ffmpeg recipe.

## Rules

- Never type, request, or store an Adobe password. `login` opens a window where the user logs in.
- Never overwrite the source file.
- Never re-encode the video. `replace` copies the picture and swaps the audio.
- The audio is uploaded to Adobe through the user's logged-in browser. Say that before the first upload in a session.
- `collect` copies a matched download. Do not delete files in Downloads.
- Do not write a second copy of these instructions into a private agent memory. This repository is the source.
- Do not write into `~/.grok`. That directory holds auth state. `python3 enhance.py install` does not touch it.

## Commands

```bash
python3 enhance.py doctor
python3 enhance.py install
python3 enhance.py prepare "path/to/file.mp4"
python3 enhance.py collect "path/to/work" --files "download.mp3"
python3 enhance.py replace "path/to/work" [--out-dir DIR]
python3 enhance.py login                    # optional, needs patchright
python3 enhance.py upload "path/to/work"    # optional: upload, download, collect
python3 -m unittest discover -s tests -v
```

## Layout

- `enhance.py` does the file work. Only `login` and `upload` open a browser, and only when patchright is installed.
- `skills/adobe-podcast-enhance/SKILL.md` is the procedure.
- Project skill links: `.claude/skills/`, `.agents/skills/`, `.gemini/skills/`. They point at the same skill. Cursor also reads `.claude/skills/`, so this repo does not add a third copy under `.cursor/` or `.codex/`.
