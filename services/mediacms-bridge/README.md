# Private MediaCMS integration bridge

This integration is pinned to MediaCMS v9.1.0 / commit
`1ab99af4587bfc515582783c9ee19ae12e0fd065`. See
`deploy/media-services/README.md` for the isolated service setup and limits.

Video processing uses the actual upstream encoding profiles and Whisper tasks.
The pinned upstream does not create standalone audio renditions. Our
`audio_preview.py` extension therefore creates an AAC/M4A rendition with FFmpeg
inside the existing supervised initialization process. It registers that file
as a real MediaCMS `Encoding` under the inactive `Inspiration audio AAC`
profile. The profile is inactive so ordinary video jobs never select it.
Deleting a temporary upload cascades through the native encoding file cleanup.

Audio processing remains incomplete until this initialization step publishes
its completion marker. Failed initialization marks its audio encoding failed;
retry dispatches initialization again through the same durable outbox and
exclusive process lease. It never dispatches audio to the native video encoder.
Whisper transcription works through the unchanged real upstream audio/video
task. Images already receive their preview during library ingestion and are
not submitted for redundant MediaCMS processing.

Actual short-media acceptance covers audio upload, AAC preview import into CAS,
Whisper subtitles, timestamped subtitle retrieval and removal of the temporary
upstream copy. These are pipeline checks, not long-media load tests or a general
transcription-quality benchmark. The opt-in acceptance lives in
`backend/tests/test_mediacms_asr_live.py` (`audio` parameter); the fixture and
expected phrase must be explicitly provided. No model download occurs in tests.

The private upload handler enforces the cumulative source byte limit before
writing each chunk, and checks free space on upload and final-media mounts.
The supervised task parent checks temporary/media free space before starting
work and at one-second wait intervals. On shortage it kills only its own child
process group and marks the stage retryable only when no other job lease is
held. `MEDIACMS_DISK_RESERVE_BYTES` defaults to 10 GiB in the deployment settings.
These are soft admission/runtime guards, not volume quotas: concurrent writes,
cross-mount storage copies and sampled checks still have race windows.
