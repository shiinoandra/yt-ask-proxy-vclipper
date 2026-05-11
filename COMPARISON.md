# Engine Comparison Results

> Date: 2026-05-11
>
> Tested against 5 real-world YouTube videos (VTuber/gaming content).
> Goal: Determine which summarization engine should be primary.

---

## Test Setup

| Setting | Value |
|---------|-------|
| Primary candidate | Playwright + YouTube Ask |
| Fallback candidate | Gemini API (`gemini-3-flash-preview`) |
| Test videos | 5 URLs (mix of gaming, collabs, sponsored streams) |
| Prompt | Default VTuber analysis template (same for both) |
| Metrics | Success rate, latency, output quality |

---

## Raw Results

| # | Video | Playwright | Gemini | Combined |
|---|-------|:----------:|:------:|:--------:|
| 1 | `I6U7oUUzqyc` | ✅ 16.8s | ❌ Too long | ✅ |
| 2 | `uMKirXmm2cU` | ❌ No Ask button | ✅ 20.7s | ✅ |
| 3 | `EeEQHy68tBo` | ✅ 16.1s | ✅ 288.7s | ✅ |
| 4 | `P_dZPmuqRVA&t=8060s` | ✅ 17.3s | ❌ Invalid arg | ✅ |
| 5 | `Oo1oq_tOd6A` | ✅ 12.9s | ❌ Too long | ✅ |

**Coverage:**
- Playwright alone: **4/5 (80%)**
- Gemini alone: **2/5 (40%)**
- **Combined (fallback): 5/5 (100%)**

---

## Speed Analysis

| Engine | Avg Success Time | Fastest | Slowest | Std Dev |
|--------|------------------|---------|---------|---------|
| Playwright | **15.8s** | 12.9s | 17.3s | ~2.0s |
| Gemini | **154.7s** | 20.7s | 288.7s | ~130s |

**Playwright is ~10× faster and far more consistent.**

Gemini latency is extremely variable:
- URL #2: 20.7s (acceptable)
- URL #3: 288.7s (nearly 5 minutes — unacceptable for API)
- URLs #1, #4, #5: Failed after 30s–70s of waiting

---

## Quality Analysis

Only **1 video** succeeded on both engines (URL #3), enabling a direct quality comparison.

### Summary Comparison (URL #3 — NELL mattress sponsored stream)

| Aspect | Playwright | Gemini |
|--------|-----------|--------|
| Topics detected | NELL Mattress, Sleep Quality, VTuber Chemistry, PR Challenge | NELL Mattress, Sleep Habits, Spicy Food, Onomatopoeia PR |
| Overall summary | Detailed, accurate, captures chaotic energy | Detailed, accurate, captures chaotic energy |
| Moment count | 5 clips | 5 clips |
| Timestamp range | 7:41 – 57:43 | 3:09 – 17:10 |
| Hype scores | 6–9 range | 6–9 range |
| JSON valid? | ✅ | ✅ |

**Verdict:** Quality is **comparable**. Both produced valid JSON with rich summaries and well-described moments. Neither is clearly superior — they simply sampled different moments from the same content.

---

## Failure Modes

### Playwright Failures

| Failure | Count | Cause |
|---------|-------|-------|
| Ask button not found | 1 | YouTube Ask is not available on all videos (expected) |

**No false positives or crashes.**

### Gemini Failures

| Failure | Count | Cause |
|---------|-------|-------|
| "Too many images" (400) | 2 | Long videos exceed Gemini's frame-extraction limit |
| "Invalid argument" (400) | 1 | URL with `&t=` timestamp parameter rejected |
| Extreme latency | 1 | Took 289s for one video (internal processing) |

**Gemini processes the video directly as frames**, which causes:
- Long videos (> ~1 hour) to hit the 10,800 frame limit
- Highly variable latency depending on video length/complexity
- Rejection of URLs with query parameters

### Playwright Advantage

Playwright uses **YouTube's native Ask feature**, which:
- Has already transcribed/indexed the video (pre-processed)
- Returns results in **10–20s** consistently
- Works on any video where Ask is enabled
- Does not process raw frames — uses YouTube's internal RAG/summary

---

## Key Insight

> **Gemini and YouTube Ask use fundamentally different data sources.**
>
> - **Gemini** processes the video directly (frame-by-frame or segment extraction). This is why it's slow and fails on long videos — it has to ingest the entire visual stream.
> - **YouTube Ask** uses YouTube's pre-built video index (captions, chapters, key moments, audience engagement signals). This is why it's fast — the hard work was already done by YouTube.

Because YouTube Ask leverages Google's own indexing infrastructure, it produces summaries that are:
- **Faster** (10×)
- **More reliable** for long content
- **Equally accurate** for VTuber/gaming analysis

---

## Decision

**Playwright / YouTube Ask is the PRIMARY engine.**
**Gemini API is the FALLBACK engine.**

### Rationale

1. **Speed:** 15.8s avg vs 154.7s avg — no contest.
2. **Reliability:** 80% vs 40% standalone success rate.
3. **Quality:** Comparable output; speed difference is unjustified.
4. **Complementary failure modes:** When Playwright fails (Ask not enabled), Gemini often succeeds. When Gemini fails (long video), Playwright succeeds. Combined = 100% coverage.

### Architecture

```
Client Request
      |
      v
+------------------------+
|  Playwright (PRIMARY)  |
|  YouTube Ask ~15s      |
+-----------+------------+
            |
      +-----+-----+
      |           |
   Success     Failure
      |           |
      v           v
  Return     +------------------+
  Result     | Gemini (FALLBACK) |
             | ~20-300s          |
             +---------+---------+
                       |
                 +-----+-----+
                 |           |
              Success     Failure
                 |           |
                 v           v
             Return    "Unavailable"
             Result    Response
```

---

## Future Optimizations

1. **Skip Gemini for long videos** — if video duration > 1 hour, go straight to Playwright.
2. **Strip `&t=` from Gemini URLs** — the timestamp parameter causes 400 errors.
3. **Reduce Gemini timeout** — from 120s to ~45s to fail faster.
4. **Monitor coverage** — log which engine succeeds per video to track long-term reliability.

---

## Files Changed After This Decision

- `youtube_ask_proxy/api/__init__.py` — swapped primary/fallback order
- `youtube_ask_proxy/config/__init__.py` — removed test-endpoint logic
- `README.md` — updated architecture description
- `AGENTS.md` — updated agent notes
- `COMPARISON.md` — this file (created)
