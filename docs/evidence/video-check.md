# Demo video check (VD-01 to VD-09)

File: `docs/ResolveRight_demo.mp4` (9.65 MB, copied from `04_Video\out\ResolveRight_demo.mp4`, where the working files stay). Checked 2026-11-03 on the local build; to be recorded again on the Render URL after deployment.

| Check | Result |
|---|---|
| Length at most 5:00 | 4:32 (272.1 s), 1920x1080, H.264 and AAC |
| Five sections in the order of the specification | Problem, Solution, Architecture, Evaluation, Demo (`video-script.md`) |
| Problem names the user, the workflow and the pain | Yes (three-step slide and narration) |
| Architecture shows the diagram | Yes, the Mermaid diagram of `docs/architecture.md`, four zoomed parts with balloons |
| Evaluation shows metrics and what improved | RAGAS 36 questions, improvement result, debugged failure, cache and guardrail numbers; states that reviews were AI-assisted |
| Demo: core flow | Refund request for ORD-1001, confirmation with reference |
| Demo: one guardrail behaviour with its log | Prompt-injection message blocked, then the `guardrail_triggered` log line |
| Demo: one gracefully handled failure | Unknown order ORD-9999 |
| Demo also shows | An escalated case on the Staff Review page and in the Developer Debug view |
| Captions and balloons | Burned in (`04_Video\captions\demo.srt`), so the video can be followed with the sound off |
| No secrets in any frame | Only the app page was recorded (headless browser recording of the page itself: no taskbar, terminal or other windows). Frames contain no key, `.env` content, Langfuse or Render page |
| Narration | Text-to-speech (Piper, voice en_US-lessac-medium) |
| Fallback clips | The four raw takes are kept in `04_Video\raw\take_A..D.webm` |

Deviation from the plan: the demo was recorded with Playwright (page recording) instead of OBS Window Capture, because it records only the page, shows no taskbar, and types the messages the same way every time. OBS Studio is installed but was not needed.
