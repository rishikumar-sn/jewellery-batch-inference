# AU Image Inspection

Local React + FastAPI jewellery review app at **http://127.0.0.1:5050**. Blue/white interface with Times New Roman. Images and inference stay on this computer.

## Start

To stop the server from another PowerShell window, run `.\stop.ps1` (default port 5050), or `.\stop.ps1 -Port 5052` for a custom port. This terminates this project's server and its child processes. An active inspection can be resumed after restarting. Use `-WhatIf` to preview the target without stopping it.

Run `Setup.bat` once, then `Start.bat`. PowerShell equivalents are `powershell -ExecutionPolicy Bypass -File .\setup.ps1` and `powershell -ExecutionPolicy Bypass -File .\run.ps1`. Setup creates an isolated `.venv` and installs pinned packages only from `offline/python`, with network indexes and caches disabled. It uses the bundled `frontend/dist`; Node.js is not needed for normal setup. The model loads before the browser opens. Stop with Ctrl+C. `run.ps1 -Port 5052` selects another port; `-NoBrowser` suppresses browser opening.

Choose a folder, wait for upload, then **Run inspection**. Open finished batches, filter findings, inspect full images and export JSON. Use **Pause** on a processing or queued batch. A processing batch finishes its current image, then shows **Resume**; continuing processes only pending images and preserves completed results. Paused time is excluded from elapsed processing time. Paused batches remain paused across app restarts. **Rerun batch** remains visible alongside processing controls; pause an active batch to enable it. It archives previous results and performs fresh analysis of every image. **Delete batch** removes it from the workspace; an archived snapshot and image data remain locally for recovery. Active/queued/pausing batches cannot be deleted or rerun.

## Offline installation on another device

For a GitHub checkout, copy the model and projector from an existing offline bundle into `models/testing/` before running `Setup.bat`. Use the filenames and revision listed under [Exact testing model pair](#exact-testing-model-pair). The checkout includes the Python wheels, llama.cpp server, and built frontend needed for offline setup.

The portable copy is `F:\AU-Image-Inspection-Offline`. This distribution ships without `.venv` or `frontend/node_modules`. Setup creates a fresh isolated `.venv` on each device and installs all Python packages from `offline/python`. The bundled frontend is already built. Copy the **entire folder**, including `offline`, `models`, `tools`, and `frontend/dist`, to the destination computer. The original D: working project remains in place.

1. Use **Windows x64 with 64-bit CPython 3.11 installed** (3.11.6 was used to prepare and verify this bundle). Python itself is a prerequisite and its installer is not included. Other Python versions, Windows ARM64, and Linux need a separately prepared package bundle.
2. When transferring an already installed copy, omit `.venv`; virtual environments cannot be moved reliably. Setup creates a fresh one on the destination. Keep `data` to carry existing inspections; these files may contain your images/results.
3. Double-click `Setup.bat`, then `Start.bat`. No package or model downloads occur during setup. To choose Python explicitly, use `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -Python "C:\Path\To\Python311\python.exe"`.

`offline/requirements-win-py311.lock.txt` pins the full Python dependency set from the working environment, including transitive dependencies. `offline/python` contains the installable wheels. Setup uses `--no-index --no-cache-dir` and an isolated venv, then checks dependencies and verifies model/runtime hashes. It fails if required files are missing instead of going online. `requirements-local.lock.txt` is the old partial environment record and is not used by offline setup.

Saved image paths are reconnected to the local `data` folder at startup when a project is moved. The F: copy uses project-relative paths for the exact testing model, projector and runtime, so it can be moved to a different drive. Its `reference/testing-artifacts.json` retains the original baseline hashes. The unused alternate model files from the working folder are omitted from this portable copy.

The built frontend is included. To rebuild it from a GitHub checkout, install Node.js (the bundle was prepared using Node 24.12.0/npm 11.6.2), then run `npm ci` and `npm run build` from `frontend/`. For a portable copy with `offline/npm-cache` populated, `.\setup.ps1 -RebuildFrontend` can rebuild it offline. Normal users do not need Node.js/npm.

To refresh dependencies later, prepare a new complete wheel set and lock file on an online Windows x64/Python 3.11 machine, populate the npm cache from `frontend/package-lock.json`, rebuild the frontend, and verify a clean offline installation before distributing. Do not delete `offline` after installation if you need to set up more devices.

## Exact testing model pair

Uses the same source and filenames as `Start-Jewellery-Qwen3.5-2B.bat`:

- Repository: `vultr/Qwen3.5-2B-GGUF`
- Revision: `eefa5164a6ef76f9a5577945555392c79a77686c`
- Portable model: `models/testing/Qwen3.5-2B-Q4_K_M.gguf` (the D: working copy keeps its existing configured path).
- Portable projector: `models/testing/Qwen3.5-2B-mmproj-f16.gguf`.
- Portable runtime: `tools/llama.cpp/llama-server.exe`, copied with its DLLs from the original testing runtime.

The model file initially had SHA-256 `2fd003e42c015a9579951c03c061373669733b8c2b084b14c0d2e9033f471e46`, differing from Vultr's published `cd1950c433492e0bbf2e64f32486c525af0e0bf2ab98dd73c329e597ef282f2d`. The projector already matches its published hash. `restore_vultr_model.py` downloads only that exact original Vultr revision, verifies it and preserves the previous file before replacement. `reference/testing-artifacts.json` records the selected files. Startup verifies their hashes. The previously downloaded Unsloth files remain unused; they are never selected as a fallback.

## Jewellery visibility inspection (policy 4)

The default `inspection` mode starts with one structured visual assessment. The maintained prompt is `backend/prompts/inspection.txt`. It inspects the main subject on the pan/tray or inside the held packet and ignores background jewellery and worn accessories. The model describes the visible evidence and reports capture source, jewellery presence, visible construction, cropping, obstruction, blur, glare/exposure and item separation. Application rules assign priority.

| Priority | Criterion |
|---|---|
| Critical | Decode failure, effectively blank frame (at least 99.5% near-black/near-white pixels), a visibly reproduced photo, or essentially no usable view of the subject. |
| High | Jewellery absent, presence cannot be established, or most construction cannot be inspected. |
| Medium | Useful construction is visible but important detail is lost to overlap, cropping, obstruction, blur, glare or exposure. |
| Low | Jewellery presence and visibility are established, including cases where construction is clear and only the count remains uncertain. |
| Unassessed | Missing/invalid/failed assessment or unresolved essential visibility uncertainty without an established substantive limitation. |

Minor findings do not accumulate. Only decode failures and effectively blank frames bypass the model. Low sharpness or extensive near-white pixels in the central inspection area can trigger a second look when the model reports unrestricted visibility. An unresolved discrepancy stays in Needs verification. A scale image without readable weight is eligible for Low.

Scale presence, exact weight, jewellery family/count, threads, beads, stones, natural coiling and transparent packaging do not add severity. Packaging and overlapping items matter only when they actually obstruct inspection. One assembled necklace with its attached pendant/beads counts as one item; individual earrings count separately. Optional family/count predictions may still be inaccurate and are not independently verified.

The first pass looks for physical jewellery and ignores printed illustrations, worn accessories and other background objects. The description and structured results use the same recorded jewellery findings. Uncertain counts and types can be left open. For scale images, the app reads the display from two views and reports the digits when they agree. Readings are shown in grams. When the display unit is missing, the app treats the reading as grams and preserves the original transcription in the export.

Essential uncertainty, malformed output, or evidence of background confusion can trigger a second look at a 640px long side. Packet subjects and uncertain subject locations can receive an independent presence check. Jewellery assessment uses up to two visual requests. Scale reading uses up to three separate requests, including a full-frame fallback when the first crop yields no reading. Results record processing status, raw attempts, token usage, timings and assessment version. Optional findings and scale-reading failures have no effect on inspection priority.

The workspace opens on **Priority review**, showing Critical/High and technical/presence verification cases. Medium and All images remain accessible separately. A deterministic hash-based approximately 5% sample of Low results is available under **Low priority audit sample**, without changing their tier. Results show the recorded policy and processing status. Existing saved results are never silently reclassified; use Rerun batch to archive and reprocess under the new policy. Resuming an old paused batch retains its completed results, so that batch may contain multiple recorded policies.

Settings: Qwen3.5-2B Q4_K_M, 8 CPU threads, 512px initial long side, 240 output-token cap, thinking disabled, persistent loopback inference process. The assessment response uses schema-constrained JSON with explicit uncertainty choices. Model startup and queue waiting are excluded from per-image inference timings. Changes require restarting the application.

There is no cached testing-report replay. The 84 original fixtures remain legacy regression evidence, not human-labelled ground truth for the new policy. Live benchmark scripts under `test-output` read image copies and never overwrite user results. Small diagnostic comparisons do not establish full-dataset accuracy; evaluate missed serious cases, false priority flags, review proportion, second-pass proportion, median and p95 latency against manually labelled images before production rollout.

Inspection priority does not establish purity, authenticity, valuation or fraud.

## Data and verification

Uploads are copied into `data/<batch-id>/`. SQLite stores batches/results; snapshots live in `data/history/`. Supported images: JPG/JPEG, PNG, WebP, BMP, TIFF (first frame/page), up to 40 MB each, including subfolders. Non-image files are ignored. Cards load 60 at a time. The app is for single-user loopback use.

For tests, first install the bundled test dependencies with `.venv\Scripts\python.exe -m pip install --no-index --no-cache-dir --find-links offline/python -r offline/requirements-test.lock.txt`, then run `.venv\Scripts\python.exe -m unittest discover -s tests -v`. Tests cover uploads, fresh reruns, deletion, recovery, strict finding filters, decoder compatibility, presence verification, field evidence reviews, description consistency, contradictory scale/packet/count combinations, and the legacy policy and decoder for all 84 saved testing fixtures. The legacy fixtures retain their original tiers because they contain no newly affected presence cases. `reference/testing-qwen35.json` is a regression fixture only, not an inference cache. New policy and one-pass/fallback tests are in `tests/test_inspection.py`. See `test-output/testing-parity-fix.md` for the earlier correction audit. The earlier `test-output/verification.md` describes the superseded first implementation.

