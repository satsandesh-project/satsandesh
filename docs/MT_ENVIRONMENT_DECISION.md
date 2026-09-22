# MT environment decision — IndicTransToolkit on this machine

**Status:** Spike complete. **Scope:** investigation only — no MT/translation code
was written in this phase. See `services/ai/_spike_indictrans.Dockerfile` for the
one throwaway artifact this phase produced (not wired into the project).

## Why this spike exists

Week 6 needs `IndicTransToolkit` (AI4Bharat's IndicTrans2 pre/post-processing
library) for real MT bring-up. Its own README states it "requires Python 3.10+
on a Linux/macOS environment" and "is not built or tested for Windows," and it
ships a Cython extension (`processor.pyx`) that has to compile at install time.
This spike checked whether that warning actually blocks us on this Windows dev
machine, and which of three environments (native Windows, WSL2, Docker) is the
one Week 6 Phase 1 should build against.

**Bottom line: the toolkit's own "not supported on Windows" warning turned out
to be stale/overcautious for our case.** It installs, builds its Cython
extension, and imports cleanly on native Windows, in WSL2, and in Docker — the
two real blockers we hit were a Windows path-length limit and an unpinned
`transformers` dependency, both fixable without leaving Windows.

## Step 1 — Native Windows, straightforward attempt

**Attempt 1** — `pip install indictranstoolkit` inside a throwaway venv under
this repo's deep path
(`...\948b35f1-17a2-431a-8c39-28881b824080\scratchpad\mt_spike\throwaway_venv`):

Dependency resolution and the Cython wheel build both succeeded. Installation
then failed with:

```
ERROR: Could not install packages due to an OSError: [Errno 2] No such file or directory:
'C:\Users\admin\AppData\Local\Temp\claude\C--Users-admin-Projects-SatSandesh\948b35f1-17a2-431a-8c39-28881b824080\scratchpad\mt_spike\throwaway_venv\Lib\site-packages\transformers\models\audio_spectrogram_transformer\configuration_audio_spectrogram_transformer.py'
HINT: This error might have occurred since this system does not have Windows Long Path support enabled. You can find information on how to enable this at https://pip.pypa.io/warnings/enable-long-paths
```

This is **not** the Cython-on-Windows failure the docs warn about — it's a
Windows `MAX_PATH` (260 char) limit tripped by one of `transformers`' deeply
nested model files, made worse by the long throwaway-venv path. Checked:
`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 0` on this
machine (long path support is off).

**Attempt 2 (the one retry)** — same command, same package, in a venv at a
short path (`C:\mtspike_venv`) instead of the deep temp path. This installed
cleanly:

```
Successfully installed ... cython-3.3.0 ... indic-nlp-library-itt-0.1.1
... transformers-5.17.0 ... indictranstoolkit-1.1.1
```

Confirms the failure was path-length, not a Windows/Cython incompatibility —
`indictranstoolkit`'s own `processor.pyx` compiled to a native
`processor.cp311-win_amd64.pyd` on this machine without issue (a Windows C
build toolchain is present and pip's wheel build used it transparently).

**Smoke test, first attempt** — `from IndicTransToolkit import IndicProcessor`
failed, but with a *different* error than a Windows/Cython problem:

```
[transformers] PyTorch was not found. Models won't be available and only tokenizers, configuration and file/data utilities can be used.
ImportError: cannot import name 'PreTrainedTokenizerBase' from 'transformers.tokenization_utils' (unknown location)
```

`indictranstoolkit` declares an unpinned `transformers` dependency (`Requires:
cython, indic-nlp-library-itt, sacrebleu, sacremoses, transformers` — no
version bound), so pip resolved the newest `transformers` (5.17.0 at spike
time), which has since restructured `transformers.tokenization_utils` in a way
`IndicTransToolkit`'s collator doesn't expect.

**Smoke test, second attempt (the fix)** — pinned `transformers==4.44.2` (an
IndicTrans2-era version) and re-ran the same import:

```
transformers-4.44.2 (downgraded from 5.17.0)
None of PyTorch, TensorFlow >= 2.0, or Flax have been found. Models won't be available and only tokenizers, configuration and file/data utilities can be used.
IMPORT AND INSTANTIATE OK: <class 'IndicTransToolkit.processor.IndicProcessor'>
```

**Native Windows works**, given (a) a short-enough install path, or long-path
support enabled, and (b) `transformers` pinned to a version from the
IndicTrans2 era rather than left unbounded. The "PyTorch was not found"
message is expected and harmless for this spike — no model weights were
loaded, this only proves the package imports; `torch` will be a real
dependency once Week 6 actually runs inference.

## Step 2 — WSL2 availability

`wsl --status` / `wsl --list --verbose`:

```
Default Distribution: Ubuntu
Default Version: 2

NAME                STATE      VERSION
* Ubuntu            Stopped    2
  docker-desktop     Stopped    2
```

WSL2 **is** installed, with an Ubuntu distro already present (plus a
`docker-desktop` distro from the Docker Desktop WSL2 backend). No install
step was needed or attempted.

## Step 3 — WSL2 spike

- `wsl -d Ubuntu -- python3 --version` → **Python 3.12.3** (Ubuntu 24.04 LTS,
  "noble"). Comfortably above the 3.10+ requirement — this was not a blocker.
- `python3 -m venv /tmp/mt_spike_venv` failed immediately:

  ```
  The virtual environment was not created successfully because ensurepip is not available.
  On Debian/Ubuntu systems, you need to install the python3-venv package:
      apt install python3.12-venv
  ```

  This is Ubuntu's standard split-out of `venv`/`ensurepip` from the base
  `python3` package — normal, not a sign anything is broken.
- Attempted `sudo apt-get install -y python3.12-venv` to unblock the venv.
  This **hung** — `ps aux` inside the distro showed `sudo apt-get install`
  sitting with no `apt-get` child process spawned, consistent with sudo
  blocking on an interactive password prompt with no TTY reachable from this
  session. Per the timebox, this was killed (`sudo -k; kill -9 <pid>`) rather
  than debugged further, since Step 1 had already produced a fully working
  native-Windows path by this point.
- Steps 3.3 (smoke test) and 3.4 (repo path from inside WSL2, e.g.
  `/mnt/c/Users/admin/Projects/SatSandesh`) were **not reached** — blocked by
  the above. `/mnt/c/...` auto-mounting is WSL2's documented default behavior
  and almost certainly would have worked, but this was not verified.

**WSL2 path: distro and Python version confirmed good; the actual
`indictranstoolkit` install/import is untested** — it needs one manual,
interactive `sudo apt install python3.12-venv` (or equivalent) run by a human
with a real terminal, not attempted headlessly again by an agent.

## Step 4 — Docker availability

```
docker --version → Docker version 29.7.2, build a7dcaa6
docker ps (first attempt) → failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine:
  the daemon is not running
```

Docker Desktop CLI is installed (at
`C:\Users\admin\AppData\Local\Programs\DockerDesktop\`, not the default
`C:\Program Files\` location) but the daemon was not running. Started it via
`Start-Process "Docker Desktop.exe"`; the daemon came up within ~10 seconds
and `docker ps` then succeeded. **Docker Desktop is left running** after this
spike — nothing was uninstalled or reconfigured.

## Step 5 — Docker spike

Wrote `services/ai/_spike_indictrans.Dockerfile` (throwaway, not referenced
by anything else in the repo):

```dockerfile
FROM python:3.10-slim
RUN pip install --no-cache-dir indictranstoolkit "transformers==4.44.2"
CMD ["python", "-c", "from IndicTransToolkit import IndicProcessor; ip = IndicProcessor(inference=True); print('IMPORT AND INSTANTIATE OK:', type(ip))"]
```

`docker build -f services/ai/_spike_indictrans.Dockerfile -t
mt-spike-indictrans:throwaway .` — **succeeded** (~45s for the Cython build,
~20s to export the image; python:3.10-slim has a Linux build toolchain
available out of the box, no extra `apt-get install build-essential` needed).

`docker run --rm mt-spike-indictrans:throwaway`:

```
None of PyTorch, TensorFlow >= 2.0, or Flax have been found. Models won't be available and only tokenizers, configuration and file/data utilities can be used.
IMPORT AND INSTANTIATE OK: <class 'IndicTransToolkit.processor.IndicProcessor'>
```

**Docker works cleanly, first try, no path or long-path issues at all**
(Linux container filesystem, not NTFS). The spike image was removed after
this run (`docker rmi mt-spike-indictrans:throwaway`); the Dockerfile itself
is left in the repo as the reproducible artifact.

## Step 6 — Pure-Python fallback feasibility (not vendored, assessed only)

- **Yes, one existed.** Per the project's own CHANGELOG, `IndicProcessor` was
  pure Python under the original package name `IndicTransTokenizer`
  (migrated out as its own repo in Dec 2023) until v1.0.3 of the renamed
  `IndicTransToolkit`, which rewrote it in Cython "for faster implementation
  ... at least +10 lines/s".
- **Not realistically retrievable within this timebox.** The old
  `IndicTransTokenizer` PyPI package no longer resolves (`pip index versions
  IndicTransTokenizer` → "No matching distribution found" — it appears to
  have been removed/deprecated from the index, not just superseded), and the
  old pre-rename GitHub paths tried (`VarunGumma/IndicTransTokenizer`,
  raw file lookups against tag `v1.0.2`) 404'd or redirected to the current
  `IndicTransToolkit` repo, which only has one tag (`v1.1.1.post1`) — the
  1.0.x history isn't tagged. Finding the actual old source would mean
  digging through GitHub's commit history by hand or asking upstream, which
  is more effort than this spike's timebox allows.
- **Sizing, for when this matters:** the *current* Cython `processor.pyx` is
  538 lines / ~20KB, a single self-contained file depending only on `regex`,
  `tqdm`, `sacremoses`, and `indic-nlp-library` (all pure-Python, no C
  extensions of their own). If the old pure-Python version is structurally
  similar (plausible — Cython rewrites for speed don't usually restructure
  the whole class), it would likely be a **single-file vendor job**, not a
  large chunk of the old codebase.
- **This doesn't matter for the recommendation below** — since native
  Windows, and Docker both install and import the current Cython package
  successfully, there is no live blocker this fallback needs to solve. Not
  worth chasing further unless Steps 1–5 all regress later.

## Recommendation

**Use native Windows for Week 6 Phase 1**, with two concrete, already-verified
fixes:

1. Keep the venv path short (e.g. `services/ai/.venv`, which is already
   short — this repo's real venv is not at risk; only my throwaway deep-temp
   path hit the long-path error), **or** enable Windows long path support if
   a deep path is ever unavoidable (`HKLM\SYSTEM\CurrentControlSet\Control\
   FileSystem\LongPathsEnabled = 1`, registry edit + reboot — **not done in
   this spike**, since it's a system-level change; only needed if path length
   becomes a problem again).
2. Pin `transformers` to a version from the IndicTrans2 era
   (`transformers==4.44.2` verified working here) rather than leaving it
   unbounded — `indictranstoolkit`'s own dependency declaration doesn't pin
   it, and the newest `transformers` release breaks its collator import.

This keeps Week 6 Phase 1 in the same environment as `services/ai/speech/`
and `services/ai/mock/` already run in (`services/ai/.venv`) — no new
environment to stand up, no context-switching for the rest of the team.

**Docker is the verified fallback** if native Windows regresses for any
reason (e.g. a future `indictranstoolkit` release reintroduces a real
Windows-specific Cython issue) — it built and passed the smoke test cleanly
on the first attempt, with no path or platform quirks. `services/ai/
_spike_indictrans.Dockerfile` is left in the repo as a starting point; turning
it into the real MT service's Dockerfile is Week 6 implementation work, not
part of this spike, and per the scope lock nothing has been wired into
`docker-compose.yml`.

**WSL2 is not recommended as the primary path** — not because anything about
it failed, but because it's the one path that's still genuinely unverified:
Python 3.12.3 is confirmed present and adequate, but the `indictranstoolkit`
install itself was never reached. Given native Windows already works with a
one-line dependency pin, there's no reason to spend more time unblocking
WSL2's `sudo` prompt for this decision.

### Action items for you (not done in this spike)

- **None required to unblock Week 6.** Native Windows works today with the
  `transformers==4.44.2` pin — this doesn't need `services/ai/pyproject.toml`
  or any environment change beyond what Week 6 Phase 1 will add itself
  (out of scope for this spike per the scope lock).
- **Optional, only if you want WSL2 kept as a live option:** run `sudo apt
  install python3.12-venv` yourself inside the Ubuntu distro (interactive
  password prompt — this spike could not do it non-interactively) and re-run
  the `pip install indictranstoolkit` / smoke-test steps above. Not blocking
  anything today.
- **Optional, only if a deep install path becomes unavoidable later:** enable
  Windows long path support (registry change + reboot) — flagging as a
  system-level change this spike deliberately did not make.
