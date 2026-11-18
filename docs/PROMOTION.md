# Channel growth & promotion ideas

Notes from the 2026-11-18 discussion. The pipeline side of each idea is in [RECOMMENDATIONS.md](RECOMMENDATIONS.md).

> YouTube changes its thresholds and policies. Check the current YouTube Help pages ("YouTube Partner Program
> overview & eligibility", "Channel monetization policies") before planning around the numbers below.

## Where we start from
- The owner's existing **real cat footage channel**: 3 years ago 20–100 views per Short. Restarted uploading in
  November 2026 and now gets **500–1,200 views**. That's an asset; don't dilute it.
- The AI facts pipeline (ideas → fact-checked script → voice → comic-style visuals → music → storyboard approval).

## 1. Know the targets (and the real risk)
| Tier | Requirements (as known 2026, verify) | Unlocks |
|---|---|---|
| Fan funding / Shopping | 500 subscribers, 3 public uploads in 90 days, **3M Shorts views in 90 days** (or 3,000 watch hours) | memberships, Super Thanks, Shopping |
| Full YPP | 1,000 subscribers + **10M Shorts views in 90 days** (or 4,000 watch hours) | ad revenue share |

**The bigger risk is the "inauthentic content" policy** (formerly "repetitious content"): mass-produced, templated,
AI-made channels can be refused monetization even after reaching the numbers. Countermeasures we have or plan:
human storyboard review, varied formats and styles, fact-checked content, your own voice (cloning), and real
footage where it fits. Always set YouTube's **altered or synthetic content** disclosure for realistic AI visuals.

## 2. Measure what you control, not views
Early Shorts views are mostly YouTube testing a video on small audiences. Judge each video by (YouTube Studio →
Analytics → the Short):
- **Viewed vs. swiped away**: aim for 70%+ who stay.
- **Average percentage viewed**: aim for 80–100%+ (replays push it over 100%).
- **Subscribers gained per 1,000 views.**

A Short with 300 views and 95% viewed is a success: it tells you what to make more of. The first 1–2 months are a
learning phase, not a scoreboard.

## 3. Use the pipeline's advantage: cheap experiments
- Treat the first **30–50 videos as experiments**. Vary **one thing at a time** and keep notes:
  - hook type: question / shocking number / "myth vs. fact" / "you've been lied to about…";
  - length: 25 vs. 35 vs. 45 s;
  - style profile: comic vs. 3D animated vs. cinematic;
  - topic family: ocean, space, animals, history, body…
- Mark winners: `shorts history mark <run> -s popular --note "92% viewed"`. The idea writer already sees popular
  entries and writes new ideas with a similar hook and payoff on different subjects.
- Reject losers on the storyboard with a reason. Rejection notes are fed to the idea writer too.

## 4. Length: test around 30 seconds
The first test videos ran 42–50 s. Shorts that are fully watched or replayed get pushed further, and 25–35 s
videos usually reach a much higher "% viewed". Test `VIDEO_TARGET_SECONDS=30` (and the loop ending, so the last
line flows into the first and invites a replay).

## 5. Keep the channels separate, and use real footage
- **AI fact videos go on a new channel.** A cat audience that swipes away space facts hurts the cat channel's
  signals, and vice versa.
- **Real footage + pipeline narration** for the cat channel: your own cat clips, with a fact-checked script,
  voice, captions and music from the pipeline ("Why your cat does THIS", "3 things your cat's tail is telling
  you"). It's authentic, avoids the inauthentic-content risk, fits the audience that's already growing, and the
  facts angle can add rewatch and share value to clips that already work.

## 6. Cadence and reach
- **1–2 uploads per day, consistently.** Bursts of 10 a day look like spam; long gaps reset momentum.
  Use scheduled publishing (`publishAt`) so it doesn't depend on you being at the computer.
- **Post the same video to TikTok, Instagram Reels and Facebook Reels.** More data per video, and other platforms
  monetize on different terms (check each one's current program requirements).
- Titles: short, curiosity-driven, no clickbait lies (the fact-check protects the channel's credibility, and false
  facts bring "this is wrong" comments that kill trust).
- Pin a comment with the source of the main fact. It builds trust and invites comments.

## 7. Before YPP: other income (optional)
- Fan funding tier (above) at 3M Shorts views in 90 days.
- Affiliate links (e.g. cat products on the cat channel) in descriptions or pinned comments; follow the platform
  rules and disclose them.
- Sponsorships come later, once there's a steady audience.

## 8. Keep it from being frustrating
- Expect many early videos at 0–300 views. That's the testing phase, not failure.
- Look at the stats **weekly**, not after every upload, and look at the retention numbers above.
- Write down what each batch tested. After 4–6 weeks, double down on the 2–3 best formats.
- Your cat channel's 5–10× growth shows that consistency plus a format that fits works.

## Pipeline work this implies
| Idea | Status |
|---|---|
| Real-footage mode: user clips replace generated images and motion | ⏸ postponed: no new footage for now |
| Funny AI edits of existing cat clips (video-to-video, e.g. Wan VACE) | ⏸ later experiment |
| `VIDEO_TARGET_SECONDS=30` test batch | ⬜ next batch |
| YouTube upload with scheduling (`publishAt`), private test first | ⬜ |
| Analytics loop: pull retention, mark `popular` automatically | ⬜ |
| Own voice (cloning via Chatterbox in ComfyUI; F5 weights are non-commercial) | ⬜ needs a voice recording |
| Cross-posting uploaders (TikTok / Reels): their APIs need an app review | ⬜ later |
