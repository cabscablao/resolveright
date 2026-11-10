# Demo video check (VD-01 to VD-09)

Main file: `docs/ResolveRight_demo_render.mp4` (10.1 MB, 4:55, copied from `04_Video\out\ResolveRight_demo_render.mp4`), recorded 2026-11-03 against the live app https://resolveright.onrender.com after a service restart (fresh demo state). The earlier local-build recording `docs/ResolveRight_demo_local.mp4` (4:32) is kept as a fallback. Both were checked against the rows below.

| Check | Result |
|---|---|
| Length at most 5:00 | Render video 4:55 (295.4 s), local video 4:32 (272.1 s); both 1920x1080, H.264 and AAC. The Render video is longer because the live app answers more slowly; idle loading time was trimmed to stay under 5:00 |
| Five sections in the order of the specification | Problem, Solution, Architecture, Evaluation, Demo (`video-script.md`) |
| Problem names the user, the workflow and the pain | Yes (three-step slide and narration) |
| Architecture shows the diagram | Yes, the Mermaid diagram of `docs/architecture.md`, four zoomed parts with balloons |
| Evaluation shows metrics and what improved | RAGAS 36 questions, improvement result, debugged failure, cache and guardrail numbers; states that reviews were AI-assisted |
| Demo: core flow | Refund request for ORD-1001 on the live app, refund of ₱2,750.00 with reference |
| Demo: one guardrail behaviour with its log | Prompt-injection message blocked, then the `guardrail_triggered` log line |
| Demo: one gracefully handled failure | Unknown order ORD-9999 |
| Demo also shows | An escalated case on the Staff Review page and in the Developer Debug view |
| Captions and balloons | Burned in (`04_Video\captions\demo.srt`), so the video can be followed with the sound off |
| No secrets in any frame | Only the app page was recorded (headless browser recording of the page itself: no taskbar, terminal or other windows). Frames contain no key, `.env` content, Langfuse or Render page |
| Narration | Text-to-speech (Piper, voice en_US-lessac-medium) |
| Fallback clips | Raw takes kept in `04_Video\raw_render\take_A..D.webm` (Render) and `04_Video\raw\` (local). The guardrail log slide in the Render video shows the log line from the local run (same rule and fields; blocked messages in the live service are logged the same way) |

Deviation from the plan: the demo was recorded with Playwright (page recording) instead of OBS Window Capture, because it records only the page, shows no taskbar, and types the messages the same way every time. OBS Studio is installed but was not needed.
