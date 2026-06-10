# stream-curator

`stream-curator` is now a minimal push-only desktop client.

Current live path:

1. collect shallow feed candidates from `bilibili`, `zhihu`, `xiaohongshu`
2. send candidates to one LLM selection request
3. store push snapshots in SQLite as `current` and `warm`
4. render a 2x3 push homepage in the Electron client
5. refresh by promoting the next warm snapshot instantly

There is no old stage1/stage2/stage3 pipeline in the active app anymore.

## Repository Shape

```text
stream-curator/
  desktop/
  frontend/
  src/stream_curator/
    cli.py
    config.py
    logging.py
    push_llm.py
    push_service.py
    push_store.py
    push_worker.py
    worker_process.py
    connectors/
    models/
  tests/
```

## Runtime Requirements

- Python 3.11
- `bili.exe`
- `zhihu.exe`
- `xhs.exe`
- Electron runtime in `desktop/node_modules/electron`
- `OPENCODE_API_KEY` in the environment

Default provider settings:

- URL: `https://opencode.ai/zen/go/v1/chat/completions`
- model: `deepseek-v4-flash`

## CLI

Bootstrap SQLite:

```powershell
$env:PYTHONPATH="F:\Games\KimKitsuragi\stream-curator\src"
python -X utf8 -m stream_curator.cli bootstrap
```

Read the current push page:

```powershell
python -X utf8 -m stream_curator.cli client push
```

Promote the next warm snapshot:

```powershell
python -X utf8 -m stream_curator.cli client push --refresh
```

Run one worker cycle:

```powershell
python -X utf8 -m stream_curator.cli worker once
```

Run the background worker:

```powershell
python -X utf8 -m stream_curator.cli worker start
python -X utf8 -m stream_curator.cli worker status
python -X utf8 -m stream_curator.cli worker stop
```

## Desktop Client

Start the Electron shell:

```powershell
cd desktop
npm start
```

The desktop app:

- reads preheated SQLite snapshots first
- falls back to background preheat when the cache is cold
- shows 6 push cards on the homepage
- opens the original URL externally
- refreshes by consuming the next warm snapshot

## Tests

The remaining tests only cover active modules:

- connector mapping
- push snapshot storage/promotion
- worker process start/stop state handling
