# Audience interaction Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add optional animated audience requests without covering spoken captions.

**Architecture:** Store an `interactions` list of action IDs with job options and clips. Generate timed ASS text and vector backgrounds alongside existing captions, with a separate safe vertical band. Reuse the current composer controls, drafts and saved templates.

**Tech Stack:** Python, ffmpeg/libass, plain HTML/JavaScript, existing Portuguese translation dictionary.

**Spec:** `docs/superpowers/specs/2026-11-23-audience-interaction-design.md` (approved in chat).

## Global Constraints

- Work in `codex/interacao-publico` in the user's current checkout.
- No new dependencies. Preserve the existing palette, no shadows, colour-only hover states.
- Default off; five independent actions; animate each selected action once, sequentially.
- Keep captions and title clear. Preserve old jobs, templates and clips.
- Run `python tests/run.py` before and after changes to `studio/`.

## Review Focus

- Malformed action lists must fail validation rather than reach ASS markup.
- Short clips must not show concurrent requests or events outside the clip.
- Captions off must leave requests enabled.
- Layouts with low captions must place requests above them.
- Older templates must clear any previously selected requests when applied.

### Task 1: Option storage and animated rendering

**Files:** `studio/jobs.py`, `studio/render.py`, `studio/live.py`, `tests/test_interactions.py`, `tests/run.py`.

**Interfaces:** `clean_options` accepts `interactions: list[str]` with IDs `subscribe`, `like`, `comment`, `share`, `save`. `build_ass` gains optional `interactions` and `language` keyword arguments. Job and live clip construction copies selections; old data defaults to `[]`.

- [x] Write and run failing behavior tests for validation, job persistence, rendering with captions off, sequential timing and safe placement in every layout.
- [x] Validate and deduplicate IDs, propagate selections into clips and rerenders, and implement motion/fade ASS events with short ready-made English and Brazilian Portuguese text.
- [x] Run `python tests/test_interactions.py`; seven tests passing.

### Task 2: Composer and templates

**Files:** `web/index.html`, `web/pt-br.js`.

**Interfaces:** `jobOptions()` serializes the checked action IDs; `setClipOptions()` restores them with `[]` for absent fields; template summaries show selections.

- [x] Add five labeled checkboxes and concise motion help beside existing options.
- [x] Preserve drafts and templates, reset absent selections, and translate visible strings.
- [x] Check JavaScript syntax and exercise the composer/template flow in a browser.

### Task 3: Verification and review

- [x] Run `python tests/run.py`; final run passed all 41 test groups.
- [x] Render a sample with captions and all selected interactions; inspect entry, hold and exit frames.
- [x] Request one independent review of the final working diff; resolve substantive findings and run affected checks.
- [x] Run `git diff --check` and leave changes available on the requested branch without publishing.

## Execution record

- Baseline started before product changes. Implementation approved in chat; execute inline without another approval round.
- Ruling: use the current checkout and existing feature branch, as requested by the user; no additional worktree.
- Ruling: do not create commits or publish as part of this request; leave the implementation reviewable in the working tree.
- Task 1: complete — seven feature tests passing, including the real live-session creation path with external recording disabled.
- Task 2: complete — browser verified optional defaults, translations, template persistence/reset, profile settings and draft inheritance; JavaScript syntax passed.
- Review: identified missing creator controls for live defaults and draft override metadata. Both corrected; browser reproduced and verified the draft scenario. No deferred issues.
- Ruling: expose the same controls in creator settings and inherit them into video/live jobs, so the live propagation has a usable configuration path.
- Task 3: complete — full suite passed after updating the template API fixture to include the new selected options. Final JavaScript syntax and whitespace checks passed. Temporary UI server stopped.
- Follow-up: the user explicitly requested committing and pushing `codex/interacao-publico`, superseding the earlier ruling to leave changes uncommitted.
