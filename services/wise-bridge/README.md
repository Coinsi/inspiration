# WISE bridge

Private integration service for Oxford VGG WISE. It **runs the real WISE feature
extractors, FAISS index builder and search services** at commit
`fcfa443fbb46eb361bb19151339338616838a5b5`. It does not provide simulated production
search, fallback embeddings, or a replacement algorithm. The unrelated PyPI
package named `wise` must not be installed.

## Current validation status

- Bridge HTTP, transaction, cancellation, retry, permission scope and snapshot
  publication are covered by the local tests.
- Those tests use an explicitly fake engine. They do **not** prove model accuracy,
  compatibility of a deployed ML environment, Chinese search quality, throughput,
  or handling of 50 hours of 4K material.
- Opt-in backend tests have also exercised real pinned SigLIP2 visual and CLAP
  audio extraction/search on short synthetic videos, including reference images
  and deletion. This confirms the local pipeline, not production retrieval
  quality or long-video throughput; representative long-video tests remain gates.
- Face/object models are opt-in and have their own weights/licenses; they are not
  reported ready merely because an adapter exists.

## Runtime boundary

The inspiration backend owns authentication, project permission checks, immutable
asset version IDs, source originals, derived previews and clip references. It
sends a proxy/image/audio file and uses the version UUID as `source_id`. This
service never fetches user-provided URLs or returns local filesystem paths.

One bridge instance has one durable SQLite queue, one worker, and one heavy
process at a time. Run **one Uvicorn worker**, not multiple replicas sharing a
volume. An OS lock prevents a second worker from recovering live jobs.

The HTTP application runs in a small Python 3.11+ environment. `WISE_PYTHON`
selects a separate WISE environment. In that environment, install WISE from
the Git URL and exact commit in `upstream.lock.json`, then install this bridge
package (without replacing WISE's pinned requirements). The bridge checks pip's
`direct_url.json` commit. An editable pinned checkout is also supported with
`WISE_SOURCE_DIR`: the checkout must be clean and imported WISE must come from it.
Upstream needs its specified torch/torchaudio versions and FFmpeg support. Follow
the pinned upstream environment file, not arbitrary latest ML dependencies.

```
python -m pip install -e services/wise-bridge
python -m uvicorn wise_bridge.api:create_app --factory --host 127.0.0.1 --port 9671 --workers 1
```

Set `WISE_BRIDGE_TOKEN` to a service secret of at least 24 characters. All
application endpoints, including `/health`, require `Authorization: Bearer ...`.
Do not expose this service to the public internet. The browser never receives the
service token. Use the backend's project permission checks and source whitelist.

| Environment | Meaning |
| --- | --- |
| `WISE_BRIDGE_DATA` | Durable root for queue, proxy files, snapshots and logs |
| `WISE_PYTHON` | Python executable containing the pinned WISE install |
| `WISE_SOURCE_DIR` | Optional clean editable checkout for revision verification |
| `WISE_BRIDGE_CAPABILITIES` | Defaults to `visual,audio`; optionally add `face,object` |
| `WISE_BRIDGE_MAX_FILE_BYTES` | Per-proxy upload limit, default 8 GiB |
| `WISE_BRIDGE_MAX_STORAGE_BYTES` | Processing workspace limit, default 100 GiB |
| `WISE_BRIDGE_MIN_FREE_BYTES` | Stop processing below this free disk, default 2 GiB |
| `FEATURE_EXTRACTOR_CONFIG` | WISE's own JSON config; bridge defaults to compile/warmup off |

Use container CPU, memory, GPU and volume quotas as hard boundaries. The bridge's
periodic storage checks are guardrails, not an OS quota. Model caches also need a
separate persistent volume/quota. First model use may download weights unless the
deployment pre-provisions an offline cache; this has not been performed by the
integration tests.

## HTTP protocol

Every project, source, and job identifier is a UUID. Main task state is `status`.

- `POST /v1/projects/{project_id}/sources/{source_id}`: multipart `file`,
  `job_id`, `media_type` (`video`, `image`, `audio`), `capabilities` (JSON array),
  `restore_after_job_id` (the latest completed deletion job) is optional only for
  an initial source generation; every new upload after restoration must carry it.
  Returns 202. Repeating an identical job is idempotent. Reusing a source version
  with different bytes, or a job ID with different input, returns 409. Pass only
  `["audio"]` for an audio source; normally `["visual"]` for an image.
- `GET /v1/jobs/{job_id}`: 404 for unknown jobs. Returns `job_id`, `project_id`,
  `source_id`, `status` (`queued`, `processing`, `ready`, `failed`, `canceled`),
  `phase`, `error`, `revision`, `generation`, and per-capability `state`/`vector_count`.
- `POST /v1/jobs/{job_id}/retry`: only failed jobs become queued. Source bytes are
  retained. Ready/canceled tasks are not implicitly rerun. Retrying an old source
  generation returns 409, so an old failed cleanup cannot delete a restored asset.
- `POST /v1/jobs/{job_id}/cancel`: prevents publication and terminates the active
  child process tree. A previously published source stays available. Deletion
  cleanup is not cancelable, to avoid abandoning an already applied tombstone.
- `DELETE /v1/projects/{project_id}/sources/{source_id}?job_id=...&source_job_id=...`: durable deletion
  job. Immediately hides the source from search, then rebuilds the remaining
  index. `source_job_id` identifies an accepted upsert in the target generation;
  it is required after any restoration. Initial generation permits omission for
  compatibility. Unknown sources still receive a durable tombstone. Deletion is
  retried with the same job if it fails; repeated known requests are historical
  lookups and never change the current generation.
- `POST /v1/projects/{project_id}/search`: JSON `{query, modality, source_ids?,
  limit?}`. `modality`: visual/audio/object; face requires a reference image.
- `POST /v1/projects/{project_id}/search/media`: multipart `file`, `modality`,
  `source_ids` (optional JSON UUID array), `limit`, `bbox` (optional normalized
  JSON `{x,y,w,h}`). Visual/face/object accepts image bytes; audio accepts audio.
- Search response: `{revision, items, has_more}`. Each item has `source_id`,
  `start_seconds`, `end_seconds`, raw `score`, `modality`, `feature_id`, `vector_id`, `bbox`,
  `match_type`. Images have null time; frame matches have equal start/end. **Do
  not treat a frame hit as an exact clip boundary or a raw score as probability.**
  At most 100 hits per request. `has_more` means the result limit was reached;
  exact total and offset pagination are not implemented. `vector_id` is the actual
  WISE vector ID as a decimal string, scoped to this project revision. Multiple
  objects in one sampled frame remain separate hits; do not deduplicate by time.
- `GET /health`: reports process/configuration separately from installation
  verification. It does not load models or assert all models are usable.

The caller's source whitelist intersects the current project's visible sources
and is translated into WISE vector IDs **before** FAISS top-k. Deleted sources are
checked again after retrieval. Empty whitelist means no results, not all sources.

Standalone images use the visual capability and return `match_type: image` with
null start/end timestamps. They never become an artificial half-second clip.
Standalone audio uses the audio capability; videos can contribute visual frames
and audio intervals. The business API preserves each source's actual media kind
and MIME when uploading its normalized preview. Image/audio capability mismatches
are rejected at both boundaries. Reference audio queries are bounded to 4 MiB
and 30 seconds, decoded locally to a real mono WAV, and searched by WISE CLAP
across standalone audio and video soundtracks. No extra model is downloaded.

`backend/tests/test_wise_multimodal_live.py` is an opt-in real acceptance of those
paths (requires `INSPIRATION_WISE_MULTIMODAL_E2E=1` plus URL/token/test database).
It verifies actual image and audio embeddings, reference-media queries, source
type/time mapping, scope and deletion. Synthetic colors/tones prove the plumbing,
not retrieval quality. Audio indexing retains upstream's 4-second chunks and
its limitations for short clips and final partial chunks.

Restoration requires a new upsert job with the same immutable bytes and an
explicit `restore_after_job_id`. The named deletion must be the latest and ready,
with no queued/running cleanup for that source. SQLite serializes restoration
against retries; a successful restore increments the source generation and keeps
it unsearchable until its new index publishes. Later capability jobs can retain
that same restore marker without incrementing again; omitting it is rejected.
Delayed old uploads and
deletions cannot act as restoration or delete a newer generation. File adoption
and obsolete-proxy cleanup share the lifecycle transaction to avoid deleting a
newly restored proxy. Existing databases migrate their lifecycle columns in place.

## Publication, recovery and limits

1. Upload bytes are streamed into the proxy store, hashed, and registered in a
   transaction. Originals are not uploaded here or modified.
2. Each build writes a new isolated project snapshot. Unchanged source payloads
   (immutable hash/path, media kind and capabilities) with the same model IDs reuse
   published WISE features. A copied database and upstream `FaissStore` are pruned
   together; vector/shot/bbox rows cascade and thumbnail ownership is removed.
   Deleted sources are never encoded again. New sources or sources with changed
   capabilities are extracted into a separate real WISE delta project, then the
   upstream merge remaps IDs into the unpublished snapshot. Source sets must be
   disjoint. Real `wise create-index` rebuilds FAISS from those feature vectors.
3. Actual source mapping and vector/index counts are validated. Only then does
   one SQLite transaction publish the new revision and ready state.
4. Failed/canceled builds never replace the published revision. On process restart
   interrupted tasks are requeued, with a three-interruption limit. Recovery starts
   that task's extraction again; **within-video frame-level checkpoint/resume is
   not yet implemented**. New snapshots are recorded in SQLite before creation;
   registered unpublished remnants are reclaimed before retry storage checks.
   Unregistered directories are preserved for manual review. Filesystem/logging
   failures cannot stop the queue consumer; interrupted claims are reconciled to
   an explicit failed task before another task is claimed.
5. Upstream has no supported deletion CLI. Deletion currently rebuilds from
   remaining proxies; after success prior revisions and unreferenced deleted
   proxy files are cleaned. Ordinary append retains one previous revision.

Current tradeoffs requiring further work:

- Build and query share the heavy-process lock. Queries return explicit 503 while
  a build is running instead of loading a second model. Separate query workers or
  chunk scheduling is needed for continuously available search during long jobs.
- Queries reuse one supervised process and encode each query only once across
  image/video indices. The default cache holds 1 model and 2 project revisions,
  with a 512 MiB total FAISS-file budget. Configure `WISE_QUERY_CACHE_MODELS`
  (1–4), `WISE_QUERY_CACHE_PROJECTS` (1–16), and `WISE_QUERY_CACHE_INDEX_BYTES`.
  This file-size budget is not an exact RAM limit; retain container memory limits.
  A new project snapshot exceeding the index budget is rejected before inspection
  and publication, retaining the previous queryable HEAD. If a deployment lowers
  the budget below an existing index, queries still reject it before loading.
  Reduce project scope or provision both the cache budget and container memory;
  IVF/sharding support requires a separate implementation and evaluation. Model-budget
  exhaustion restarts the child to release WISE's process-global model cache.
  Timeout, cancellation, protocol failure, or query errors also terminate the
  child; the next request starts cleanly. Cold requests still load weights.
- Before every build the resident query process is stopped under the shared
  heavy-task lock. Snapshot publication/cleanup cannot overlap cached readers.
  The cache keys include project and immutable revision, never just project ID.
- Changes to a source's capabilities re-extract that source, not the unaffected
  project members. An immutable model-ID change also prevents affected feature
  reuse. Optional face/object features additionally require the same verified
  manifest digest (covering weights, dependencies and pinned source identity).
  Legacy or missing fingerprints never authorize upsert reuse; a changed digest
  invalidates affected features, and another check before publication rejects
  changes during a build. Pure cleanup copies the historical fingerprint without
  reading model files, so removing weights cannot prevent feature deletion. If
  its published engine is missing, cleanup fails rather than starting inference.
  The current delta uses its sources' union of requested capabilities;
  per-source optional model isolation inside a multi-source delta is not implemented.
- Feature files are copied for atomic publication; disk needs more than one index
  copy plus temporary delta/filter files. Total disk guardrails include temporary
  files; the final query budget counts both real feature shards and index files.
  Copying/filtering and `IndexFlatIP` rebuilding remain linear in project size;
  this avoids repeated model inference, not all deletion cost. Large libraries need
  measured memory limits and an evaluated IVF configuration.
- WISE results are per sampled frame/audio chunk, not a scene tree. Transcript
  generation/search and shot detection are owned by other integration stages.
- Deployment GC/retention of completed task logs and canceled unused uploads still
  needs an operational retention policy. Disk guardrails reject new work when full.

The pinned upstream has an audio-only index-loader enum typo. The narrow adapter
in `upstream_compat.py` binds `MediaType` to `ModalityType` only inside that one
loader function, where the verified source has exactly one `MediaType` reference.
It requires WISE commit `fcfa443fbb46eb361bb19151339338616838a5b5` and exact function
SHA-256 `25df41f17ea2009162936238712075059eb57251e7fd59290b370a4166f7d370`.
An upstream revision/structure change fails explicitly until reviewed; no encoder
or retrieval algorithm is replaced, and the upstream checkout stays unmodified.

Incremental merge also uses two commit/function-hash guarded corrections in a
private function namespace: `_merge` must not compare whole-image NULL timestamps
with numbers (`2c3e58a7dcbdd3a3b4535d1c361a8e5661ec3373152bc4c3058da70086be4220`),
and `prepare_filter_stmt` must pass the executable SQLAlchemy Select rather than a
compiled SQLiteCompiler to `Connection.execute`
(`ecc00f72a83c660e4927c7fdb3a3da8b1a07c2e6d3e19833b2cbc45738e1e634`).
Optional bbox metadata classes receive this same private SQL-filter binding;
neither upstream modules nor encoder classes are globally modified. We call the
real `_merge` operation directly because the public upstream `merge` wrapper
swallows errors and attempts in-place rollback. Any bridge merge exception instead
fails the build and discards only its registered unpublished snapshot.

`INSPIRATION_WISE_STORAGE_TESTS=1 python tests/test_feature_reuse_upstream.py -v`
runs real pinned WISE/SQLite/FAISS storage regressions without loading models.
They verify vector IDs/values, NULL image timestamps, same-frame bbox metadata,
thumbnail cascades, actual post-delete index lookup and unchanged source snapshots.
The tiny explicit vectors are storage fixtures, not semantic retrieval evidence.
Real-model multi-source and lifecycle acceptance must still follow deployment.

Run bridge tests from this directory with `python -m pytest`. For actual acceptance,
use a pinned real WISE installation, real visual/audio samples with known search
answers, append/delete/cancel/restart cases, and measured long-video batches.

## Pinned visual smoke test

`scripts/prepare_visual_model.py` prints a download manifest by default. With
explicit `--download`, it fetches only SigLIP2 visual weights and tokenizer files
from `timm/ViT-B-16-SigLIP2-512` at revision
`5e7f4f980e465036486ce78dfc721bbedf291939` (about 1.54 GB). Run it in the isolated
WISE environment with the persistent HF cache mounted. It requires an open_clip
version that prefers safetensors, then pins this model's local `main` cache ref.
Keep `HF_HUB_OFFLINE=1` for inference after preparation; no duplicate `.bin`, audio,
face, or object model is downloaded by the script.

The backend test `tests/test_wise_live.py` runs only when
`INSPIRATION_WISE_E2E=1`, `INSPIRATION_WISE_URL`, `INSPIRATION_WISE_TOKEN`, and
`TEST_DATABASE_URL` are supplied. It creates short real FFmpeg red/blue footage,
uses a disposable database schema and actual bridge inference, checks nonempty
indices, text and extracted reference-image queries, valid version/time results,
cross-project and whitelist isolation, deletion and repeated cleanup.
`INSPIRATION_WISE_TIMEOUT_SECONDS` defaults to 600. Source cleanup runs
in `finally`. This is pipeline acceptance, not a semantic quality/recall benchmark;
Chinese query ordering is recorded rather than being advertised as validated.

Audio preparation is separate and remains opt-in. See [AUDIO.md](AUDIO.md) for
the CLAP 2023 + GPT-2 dependency chain, exact license metadata and cache hashes,
and `scripts/prepare_audio_model.py` (default: manifest only). The audio-only
acceptance test `backend/tests/test_wise_audio_live.py` additionally requires
`INSPIRATION_WISE_AUDIO_E2E=1`; preparing files does not enable audio in the UI.
