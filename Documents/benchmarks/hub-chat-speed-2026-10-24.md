# Super Brain speed benchmark — 2026-10-24

**Historical experiment — no longer active.** The user subsequently requested CREAO's saved agent model and skills without application overrides. The local model setting was removed, the chat route no longer reads it, and skill/tool suppression and the forced chat analysis depth were removed. The timings below describe the experimental configurations, not the current one.

The fastest tested combination in this experiment was a shorter direct-answer request plus a top-level `chatModelId` request for `gemini-3-flash`. No separate Gemini service was called; all six runs used CREAO.

## Live results

Six sequential paid CREAO hub runs used the same two questions, synthetic A/B findings, account, and one-second polling. These times measure the provider adapter's whole request, including run submission, polling, and network overhead; they exclude browser rendering. The factual and comparison questions were interleaved across variants to limit simple ordering effects.

| Request configuration | Factual question | A/B comparison and calculation | Median of these two questions | Answer checks |
|---|---:|---:|---:|---|
| Previous prompt, saved default model | 11.752 s | 60.855 s | 36.303 s | Both passed |
| Shorter prompt, saved default model | 49.501 s | 50.037 s | 49.769 s | Both passed |
| Shorter prompt, `gemini-3-flash` requested | **10.975 s** | **13.795 s** | **12.385 s** | Both passed |

The smaller prompt alone did not improve these tests. The selected combination reduced the median of these two cases by about 66%, but two samples per variant do not establish a general latency distribution or guarantee future timings.

## Execution versus delivery time

CREAO's own `startedAt` and `completedAt` timestamps separate agent execution from the rest of the request:

| Request configuration | Factual execution | Comparison execution |
|---|---:|---:|
| Previous prompt, saved default model | 11.132 s | 35.232 s |
| Shorter prompt, saved default model | 20.591 s | 20.672 s |
| Shorter prompt, `gemini-3-flash` requested | 10.115 s | 13.053 s |

All six recorded queue intervals were under 0.03 seconds. Some default-model calls had roughly 26–29 seconds of additional submission, polling, or network delay beyond the recorded execution interval. The benchmark does not isolate which remote HTTP stage caused that overhead. The comparison's execution itself also improved, from 35.232 to 13.053 seconds; the measured gain is not solely delivery overhead.

The existing API accepts the `gemini-3-flash` model request, but its run response does not echo the serving model. These results establish the behavior of that requested configuration, not independent verification of the underlying serving model.

## Knowledge and answer checks

Both records described the same explicit 2D heat scheme with alpha = 0.001:

- Researcher A's Aster-17: dx = dy = 0.01, dt = 0.02, observed finite decay.
- Researcher B's Beryl-23: dx = dy = 0.005, dt = 0.01, observed blow-up.

The factual question asked for Aster-17's actual time step. The comparison asked the hub to combine the two observations with scientific reasoning, explain their agreement, and derive Beryl-23's stability limit. All answers identified dt = 0.02 for A, and the comparison answers identified dt <= 0.00625 for B (including equivalent scientific notation). The selected comparison answer named both trials, distinguished observations from theory, and retained hypothesis labels. This is a narrow quality check, not a comprehensive scientific evaluation.

Full answers, run IDs, prompt lengths, and timing metadata are in [the saved results](hub-chat-speed-2026-10-24.json). No real project data or API credentials are included.

## Applied changes

- Send `CREAO_HUB_CHAT_MODEL_ID` as top-level `chatModelId`; omit it when unset, preserving the agent's saved default. This matches the [CREAO API Trigger options](https://community.creao.ai/AI_hackathon_UCLA2026/tech).
- Remove duplicated response guidelines and shorten the instructions. The factual prompt fell from 3,393 to 2,382 characters; the comparison prompt fell from 3,599 to 2,588. Full selected research summaries, provenance, verification labels, current question, and recent conversation are retained.
- Ask the hub for direct research question answering from inline context, without an audit, tool calls, or skill/manifest inspection. This changes request instructions, not the saved CREAO agent workflow.
- Keep one CREAO-generated answer for every message and retain existing project access checks.

The available app-trigger key could not read/edit the hosted agent configuration or list the model catalog (403). Streaming and saved-agent edits require another integration, so neither was attempted after the user chose to keep the existing connection. The [API documentation](https://docs.creao.ai/pro/api-trigger) describes the separate Developer Console credentials and realtime interface.

## Current behavior

Restart `run_demo.py` to load the restored configuration. Super Brain uses the configured CREAO hub and its saved model and skills. `CREAO_HUB_CHAT_MODEL_ID` is not read by the chat route. Personal agents A/B remain CREAO agents, and shared findings are retrieved into the hub's requests.

The faster experimental timings above are not a current speed guarantee. The earlier experiment passed 282 mocked tests, with 1 Windows symlink test skipped.
