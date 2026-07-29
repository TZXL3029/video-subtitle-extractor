# Long Work Plan: short subtitle roi rescue

Created: 2026-07-29T08:46:19+00:00
Project: `D:\video-subtitle-extractor`
Mode: stewarded
Task size: Medium by default when ambiguous; update to Small, Medium, or Large after reading.

## Goal

- User outcome: fix the batch auto ROI path so fixed-position subtitles with short duration can still be considered true main subtitles instead of being excluded before OCR.
- Acceptance criteria: short stable candidates can enter `iter_candidate_subtitle_areas`; ROI rescue increases sampling when the first pass has no usable candidate or text matching is weak; successful rescue extraction records the selected ROI JSON; behavior is covered by lightweight tests.
- Non-goals: do not rerun heavy OCR/VideoSubFinder on real videos in this recovery slice; do not rewrite the already resumed implementation.

## Task Size

- Classification: Medium
- Sizing reason: existing changes touch ROI scoring, batch orchestration, and documentation; this recovery slice only adds focused verification and checkpointing.

## Context Read

- Project instructions: no project-local `AGENTS.md` was present.
- Existing patterns: `backend/tools/auto_subtitle_area.py` owns ROI candidate scoring and JSON; `scripts/batch_auto_extract.py` owns batch ROI detection, multi-candidate OCR, and label matching; `docs/batch-auto-extraction-guide.md` documents expected batch behavior.
- Similar modules/tests: no existing Python unit test suite was present; `test/` mainly contains sample media files.

## Fact Placement

- Stable facts -> README.md / AGENTS.md / docs/: existing interrupted work already updated `docs/batch-auto-extraction-guide.md`.
- Decision facts -> DECISIONS.md / docs/adr/: no ADR needed for this narrow heuristic/rescue adjustment.
- Process facts -> logs/YYYY-MM-DD.md: recovery and verification are recorded in `logs/2026-07-29.md`.
- Agent execution state -> this run directory only: checkpoint and handoff are stored here.

## Plan-To-Milestone Mapping

- Recovery inspection: non-commit checkpoint activity.
- Focused test coverage: verified milestone slice.
- Final Git/status review: non-commit checkpoint activity.

## Milestone Branch Loop

- Base branch: `main`
- Milestone branch: not created in this recovery slice because the worktree already contained interrupted uncommitted business-code changes from the prior Codex session.
- Commit/merge decision: leave unstaged for review unless the user asks to commit; avoid mixing previous interrupted changes with this resumed verification slice.

## Milestones

Each milestone should be a recoverable slice with a goal, verification, commit decision, and rollback path.

1. Milestone: short subtitle ROI rescue verification
   - Goal: prove the resumed short-duration subtitle handling works at the unit level.
   - Files/modules: `test/test_short_subtitle_roi_rescue.py`, `backend/tools/auto_subtitle_area.py`, `scripts/batch_auto_extract.py`.
   - Verification: bundled Python `unittest` and `py_compile`.
   - Compression checkpoint: `HANDOFF.md` refreshed after verification.
   - Commit/merge decision: no commit created; user asked to inspect and continue without repeating previous changes.
   - Rollback/safest undo: remove the added test and agent-run/log files if only business-code changes should remain.

## Slices

1. Slice: lightweight unit coverage
   - Milestone: short subtitle ROI rescue verification
   - Files/modules: `test/test_short_subtitle_roi_rescue.py`
   - Verification: `python -m unittest test.test_short_subtitle_roi_rescue`
   - Stop condition: tests cover short candidate admission, min-confidence bypass, rescue sample growth, duplicate filtering, rescue JSON merge, and no-label-matcher JSON writeback.

## Verification Plan

- Targeted: `python -m unittest test.test_short_subtitle_roi_rescue`
- Broader: `python -m py_compile backend/tools/auto_subtitle_area.py scripts/batch_auto_extract.py test/test_short_subtitle_roi_rescue.py`
- Manual: real-video OCR/VideoSubFinder smoke was not run in this slice.

## Risks And Rollback

- Risks: no full dependency environment was available under bundled Python (`shapely` missing), so tests use a narrow `SubtitleArea` stub and do not exercise the full OCR stack.
- Rollback/safest undo: remove `test/test_short_subtitle_roi_rescue.py`, `logs/2026-07-29.md`, and this run directory if the project should keep only the prior business-code diff.
