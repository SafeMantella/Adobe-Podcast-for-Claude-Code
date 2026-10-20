---
name: adobe-podcast-enhance
description: >
  Remove noise from a video or audio file with Adobe Podcast Enhance Speech,
  then put the cleaned audio back. On a video, the picture is copied and only
  the audio is replaced. Use when the user asks to denoise, clean audio,
  remove background noise, enhance speech, run Adobe Podcast, tirar ruído,
  or limpar áudio.
---

# Adobe Podcast Enhance

The CLI lives at the repository root, next to the directory that contains this file. Call it `python3 enhance.py` from that root, or with the absolute path of `enhance.py`. Do the steps below in order. Do not invent a different ffmpeg recipe.

The audio is uploaded to Adobe by the browser session the user is already logged into. This tool has no API key and no password.

## Before the first upload in a session

Tell the user, in one sentence, that the audio will go to Adobe Podcast Enhance in the browser where they are logged in. If they already asked for that, continue. If you are not sure they know, ask one yes/no question. Stop if they say no.

Never type, request, or store an Adobe password. If the page is a login wall, stop and ask the user to log in themselves.

## 1. Prepare

```bash
python3 enhance.py prepare "PATH"
```

- Add `--plan premium` only when the user says they have the paid plan.
- Add `--max-mb 0` when you can hand the browser a local path with no small upload cap. The default `--max-mb 9` keeps each part under 9 MB, which fits browser tools that reject files near 10 MB. That 10 MB cap is the tool's, not Adobe's.
- The free plan, as published on <https://podcast.adobe.com/en/features> on 2026-10-08: audio only, one file at a time, 30 minutes and 500 MB per file, about 1 hour per day. Premium on the same page: 2 hours and 1 GB per file, about 4 hours per day, bulk upload, video upload, strength control. If this date is more than a few months old, open that page and use the numbers written there.
- This pipeline always uploads audio and copies the original picture back. It does not upload the video, including on the paid plan. Adobe's video download is a separate encode. The point of `replace` is to keep the original picture.

Read the command's stdout. It prints the work directory and one upload path per part. A `hard-cut` warning means a split did not land on silence. Tell the user which part, then continue.

## 2. Upload and download

Open <https://podcast.adobe.com/en/enhance>.

Upload one part at a time, in index order. Wait until that part's Download control is available, download it, and only then send the next part. The free plan has no bulk queue. One at a time also keeps each download tied to one part.

If `patchright` is installed (`pip install patchright && patchright install chromium`), the script can do this step and step 3 by itself:

```bash
python3 enhance.py login              # once. The user logs in to Adobe in the window. The session is saved in .browser-profile/ (or ADOBE_PROFILE)
python3 enhance.py upload "WORK"      # uploads each part in order, waits, downloads, then runs collect
```

`upload` has no 10 MB cap, so `prepare --max-mb 0` fits it. The window opens off screen. Add `--show` to watch it. If it says the file input did not appear, the session expired: run `login` again. If it times out on a long part, raise `--timeout-min` (default 15). If the Adobe page changed, adjust `SEL_FILE`, `SEL_DONE`, and `SEL_DOWNLOAD` at the top of `enhance.py`. When `upload` prints `clean: ...`, go to step 4.

If you can operate the logged-in browser yourself:

1. Find the file input (`input[type=file]`). The visible Choose button is not the input.
2. Set that input to the part file.
3. Wait until a control named Download or Baixar is available. Processing can take about as long as the audio. If a browser call times out, look at the page again. The job may already have finished. A transcript control can appear around the same time. Do not download the transcript.
4. Click Download by its accessible name. If that click does not start a download, measure the control and click its center. Do not reuse pixel coordinates from another screen.
5. Find the new file. It may take up to a minute. Look in `~/Downloads` and in the working directory. The saved name is often a normal `.mp3`. Browser automation sometimes leaves an extension-less temp such as `.com.brave.Browser.*` or `.com.google.Chrome.*`. The file is complete even without an extension. A second click saves a duplicate. Leave unrelated downloads where they are.
6. Remember that path. It belongs to this part index.

If you cannot operate a browser, print the upload paths and this checklist, then stop until the user says the downloads are done and where they landed:

1. Open <https://podcast.adobe.com/en/enhance> and log in.
2. Upload the first mp3. Wait until Download or Baixar appears. Download it.
3. Repeat for each part, one at a time.
4. Reply with the downloaded paths, in part order.

Enhance returns the same format it was given. This tool uploads mp3. A valid `.m4a` was rejected in real use with "extension or content not currently supported", so do not upload m4a even if a format list mentions it.

## 3. Collect

Pass the downloads in part order:

```bash
python3 enhance.py collect "WORK" --files "part-01-download" "part-02-download"
```

`WORK` is the work directory `prepare` printed. If you omit `--files`, collect looks in `~/Downloads` (or `ADOBE_DOWNLOADS`) for audio newer than prepare and matches it by duration, within 0.5 seconds. Parts whose durations are within 1 second of each other are not matched that way. Pass `--files` in order for those.

A file that is byte-identical to the upload is rejected. That means the download was the original, not the enhanced file.

When every part is matched, collect prints `clean: ...`. If it exits without that line, stop and fix the missing part. Do not run replace yet.

## 4. Replace

```bash
python3 enhance.py replace "WORK"
```

- `--out-dir DIR` writes the output into `DIR` instead of next to the source.
- Video: the output is next to the source, named `<stem>-enhanced.<ext>`. The video stream is copied. The audio is AAC at 320 kbps. `replace` prints `video-stream: copied`. If it does not, the output was not kept and the source was not modified. Report the error.
- Audio: the output is `<stem>-enhanced.flac` when parts were joined, or the enhanced mp3 when there was one part.
- The source file is never overwritten. If the enhanced name already exists, `replace` stops. Pass `--force` only to overwrite that previous enhanced file, after the user agrees.
- Subtitles and other non-audio streams are copied. Extra audio tracks are dropped. `prepare` warns when the source has more than one audio stream.

## 5. Report

Tell the user:

- the output path
- the duration line `replace` printed
- `video-stream: copied` when the source is a video
- that the original file is still in place

Do not delete the source, the work directory, or files in Downloads.

## Commands

```bash
python3 enhance.py doctor
python3 enhance.py status "WORK"
python3 enhance.py prepare "PATH" --plan premium --max-mb 0
python3 enhance.py collect "WORK" --files "a.mp3" "b.mp3"
python3 enhance.py replace "WORK" --out-dir "DIR"
python3 enhance.py login
python3 enhance.py upload "WORK"
```
