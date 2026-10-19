# isnotwhatyouthink

Automated YouTube Shorts. Every video takes one subject and overturns what
you assume about it, opening with the channel's one fixed line:

    <SUBJECT> IS NOT WHAT YOU THINK        (a thing)
    <SUBJECT> IS NOT WHO YOU THINK         (a long-dead person)

4-8 shorts a day, the number chosen at random per day, posted across eight
slots aimed at Australian, UK and US evenings.

## How a run works

    plan    -> is this slot one of today's? (seeded from the date, so all
               eight runs agree without shared state)
    pool    -> Wikipedia's most-viewed, Reddit TIL and ELI5, Hacker News.
               Google Trends is a RANKING signal only: its daily list is
               names and tragedies, the wrong shape for this format.
    gate    -> an LLM approves or rejects every candidate and supplies the
               angle. Nothing is built without it. It fails CLOSED.
    topic   -> widen the approved subject into 8-ish readable sources
    script  -> 30s, fixed opening line, held to the gate's angle
    render  -> stock imagery, karaoke captions, music bed, reveal sound
    youtube -> upload, public

## What the pool will not make a video about

Hard-dropped before the gate ever sees it: anyone's death, crime, trial or
illness; anyone who died within 20 years; living people; war, disasters
and politics; countries, cities and empires; ethnic groups, religions,
castes and any other group of people; alcohol, gambling and tobacco; film,
series, game and album releases; navigational queries and lottery draws.

A physical thing from history is fine — Roman concrete, the Rosetta Stone,
Greek fire. An object or a phenomenon can be surprising without
characterising anybody; a nation or a people cannot.

The regex layer **ranks and hard-drops. It does not clear anything.** Three
recently dead people topped an early pool because a regex verdict was
mistaken for approval. `SHORT` means shortlisted; the gate decides.

## Launch

### 1. Create the repo

Public. Not for Instagram's sake — there is no Instagram here — but
because a private repo caps GitHub Actions at 2,000 minutes a month, which
becomes the limit at around ten shorts a day. Secrets live in GitHub
Secrets either way, and nothing sensitive is in the code.

    gh repo create sujee119/isnotwhatyouthink --public --clone
    cd isnotwhatyouthink
    tar -xzf ~/path/to/inwyt.tar.gz
    git add -A && git commit -q -m "isnotwhatyouthink pipeline" && git push -q

### 2. Secrets

    gh secret set GEMINI_API_KEY      -R sujee119/isnotwhatyouthink
    gh secret set PEXELS_API_KEY      -R sujee119/isnotwhatyouthink   # optional, better imagery
    gh secret set TELEGRAM_BOT_TOKEN  -R sujee119/isnotwhatyouthink   # optional, failure alerts
    gh secret set TELEGRAM_CHAT_ID    -R sujee119/isnotwhatyouthink   # optional

The same Gemini key as the reels repos is fine — it serves both the script
and the voice.

### 3. Bind the YouTube channel

Run this in a **fresh incognito window** and pick the @isnotwhatyouthink
channel at the chooser. A refresh token is tied permanently to whichever
channel is selected there; getting it wrong is how two science reels ended
up on the Korea channel.

    python3 tools/youtube_bind.py sujee119/isnotwhatyouthink

It prints the channel name back and refuses to write the secrets until you
confirm it.

### 4. Variables

    gh variable set YT_ENABLED     --body 1  -R sujee119/isnotwhatyouthink
    gh variable set YT_CATEGORY_ID --body 27 -R sujee119/isnotwhatyouthink
    gh variable set GEMINI_TTS_VOICE --body Charon -R sujee119/isnotwhatyouthink
    gh variable set DAILY_MIN --body 3 -R sujee119/isnotwhatyouthink
    gh variable set DAILY_MAX --body 4 -R sujee119/isnotwhatyouthink

Category 27 is Education.

**Start at 3-4 a day.** A brand-new channel uploading eight shorts daily
reads as spam to YouTube's systems before it reads as a channel to
anybody. Raise `DAILY_MIN` and `DAILY_MAX` to 4 and 8 after a fortnight,
once there are retention numbers to look at.

### 5. Music

    cp ~/singlescape/scidaily-reels/assets/music/*.mp3 assets/music/
    cp ~/singlescape/k-newsreels/assets/music/*.mp3 assets/music/
    git add assets/music && git commit -q -m "music beds" && git push -q

One file works; several is better, since the renderer picks at random and
the same bed on every video is a staleness signal. The reveal sound needs
nothing — it is synthesised by ffmpeg on first run.

### 6. Check before letting it loose

    set -a && source .env && set +a      # or export GEMINI_API_KEY=...
    python3 run.py doctor                # what is configured
    python3 run.py plan                  # today's target and slots
    python3 run.py pool                  # today's candidates, builds nothing
    python3 run.py build --slot 0 --dry-run

The dry run does everything except upload, and leaves the mp4 in
`out/<slug>/short.mp4`. Watch it before enabling the schedule.

### 7. Let it run

The eight crons are already in `.github/workflows/build.yml` and start on
their own once pushed. To force one immediately:

    gh workflow run build.yml -R sujee119/isnotwhatyouthink

## Tuning without a redeploy

Every setting is a repo variable, so these take effect on the next run:

| variable | default | what it does |
|---|---|---|
| `DAILY_MIN` / `DAILY_MAX` | 4 / 8 | shorts per day, randomised between them |
| `TARGET_SECONDS` / `MAX_SECONDS` | 30 / 36 | narration length |
| `GEMINI_TTS_VOICE` | Charon | `python3 tools/voice_audition.py` to compare |
| `SFX_STYLE` | whoosh | `whoosh`, `riser` or `drop` |
| `SFX_GAIN` / `SFX_AT` | 0.7 / 0.55 | reveal loudness and where it lands |
| `WIKI_DEPTH` | 200 | how deep to read the most-viewed list |
| `YT_PRIVACY` | public | `private` while you are still checking quality |
| `GATE_ENABLED` | 1 | leave it on; off means nothing can build |

## Commands

    python3 run.py pool      today's candidates and why each was dropped
    python3 run.py plan      today's target and which slots post
    python3 run.py build     build one short and upload it
    python3 run.py doctor    configuration check
    python3 run.py list      what has been made

## Exit codes from `build`

    0   built and uploaded
    2   nothing to build: not this slot's day, or no topic was approved
    1   something failed

The workflow treats 2 as success, so a quiet day is green rather than red.
