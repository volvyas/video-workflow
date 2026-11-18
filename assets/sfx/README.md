# Sound effects for scene cuts

The pipeline plays **one random file from this folder at every scene cut** (`SFX_DIR`), starting `SFX_LEAD` = 0.12 s
before the cut, at `SFX_VOLUME` = 0.35 under the voice. With 8 scenes that's 7 effects per video, so they must be
short, subtle and varied. A video with the same loud whoosh 7 times sounds cheap.

## What every file should be
- **Short:** 0.3–1.2 s, with a quick fade-out at the end and no silence at the start.
- **Peak early:** the main hit or the loudest part of the swoosh at **0.10–0.20 s** from the start, so it lands on the cut.
- **No voices, no music, no melody** (a pitch of its own fights the background music).
- **Level:** peaks around −3 dBFS, all files roughly equally loud. Mono or stereo, WAV/MP3/FLAC/OGG, 44.1 or 48 kHz.
- **Names:** anything; files are picked at random. Prefix them by type if you like (`whoosh_01.wav`).

## The set (about 10 files is plenty)
| # | File | Length | Description | Use |
|---|---|---|---|---|
| 1 | `whoosh_soft_01` | 0.5 s | Soft airy swish, like a hand sweeping past the mic; bright, no low end | default cut |
| 2 | `whoosh_soft_02` | 0.6 s | Same family as 1, slightly lower and slower, so repeats don't sound identical | default cut |
| 3 | `whoosh_deep` | 0.8 s | Deeper, fuller air swoosh with a little low-mid body, cinematic but not boomy | bigger moments |
| 4 | `swish_cloth` | 0.4 s | Quick fabric/paper flick, very dry, close-miked | fast cuts, light topics |
| 5 | `pop_soft` | 0.3 s | Short rounded "pop", like a cork or a finger on a cheek, no echo | cartoon / comic style |
| 6 | `tick_click` | 0.2 s | Crisp single mechanical click (camera shutter or switch), very short | list-like facts |
| 7 | `impact_low` | 1.0 s | Muffled low thud with a short tail, felt more than heard, no metal ring | hook, reveals |
| 8 | `riser_short` | 0.9 s | Very short noise riser that peaks at the end and cuts off (reverse cymbal feel) | before a twist |
| 9 | `shimmer` | 1.0 s | Airy sparkle/glint, high and quiet, soft attack | space, wonder |
| 10 | `water_swoosh` | 0.7 s | Underwater-ish muffled swoosh with a few bubbles | ocean topics |

The pipeline picks randomly and doesn't yet know which effect suits which topic. If you later want topic- or
style-specific sets, ask: a profile could point `SFX_DIR` at its own folder, e.g. `assets/sfx/comic/`.

## Where to get them (free for commercial use)
- **Freesound.org:** filter by **CC0** license only (CC-BY needs attribution in every description).
- **YouTube Audio Library → Sound effects** (free for YouTube videos, no attribution).
- **Pixabay sound effects** (Pixabay content license: free, no attribution; read the current terms).

## Or generate them on the AI box
ComfyUI's *Stable Audio 3* template can make them from a text prompt. Note that its **Stability Community License
has a revenue limit for commercial use**: check the current terms before monetizing. One prompt per file, then
trim to the length above:
- `soft airy whoosh sound effect, swish past the microphone, clean, no music, 0.5 seconds`
- `deep cinematic air swoosh transition, short, no reverb tail, no music`
- `short muffled low impact thud, sub hit, no metal, no music`
- `quick dry fabric flick swish, close, foley`
- `single crisp camera shutter click, foley, dry`
- `short reverse cymbal riser ending abruptly, transition effect`
- `soft cartoon pop sound, rounded, short, dry`
- `high airy sparkle shimmer glint, soft, short, magical`
- `muffled underwater swoosh with small bubbles, short transition`
