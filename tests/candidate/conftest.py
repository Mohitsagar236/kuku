"""Builders for synthetic episodes (all content invented)."""

from __future__ import annotations

from promo_common.schemas import Beat, BeatType, Episode, Scene, Show

D, A, S, C, _OTHER = (
    BeatType.DIALOGUE,
    BeatType.ACTION,
    BeatType.SOUND_FX,
    BeatType.CAMERA,
    BeatType.OTHER,
)

# (type, start, end, text) or (type, start, end, text, speaker, tags)
Spec = tuple


def make_episode(number: int, specs: list[Spec], notes: str = "", logline: str = "L") -> Episode:
    ordered = sorted(enumerate(specs), key=lambda t: (t[1][1], t[0]))
    beats = []
    for seq, (_, sp) in enumerate(ordered):
        typ, s, e, text = sp[:4]
        speaker = sp[4] if len(sp) > 4 else ("ANA" if typ is D else None)
        tags = list(sp[5]) if len(sp) > 5 else []
        beats.append(
            Beat(
                id=f"E{number:03d}-S01-B{seq:03d}", episode_number=number, scene_idx=1, seq=seq,
                type=typ, speaker=speaker, speaker_raw=speaker, emotion_tags=tags, text=text,
                start_s=s, end_s=e,
            )
        )  # fmt: skip
    end = max(b.end_s for b in beats)
    return Episode(
        number=number, source_file=f"episode-{number:03d}.txt", logline=logline,
        continuity_notes=notes, duration_s=end,
        scenes=[Scene(idx=1, start_s=0.0, end_s=end, beats=beats)],
    )  # fmt: skip


def make_show(*eps: Episode) -> Show:
    return Show(slug="synthetic", title="Synthetic", source_hash="0" * 64, episodes=list(eps))


_VOCAB = [a + b for a in ("ba", "ko", "ri", "mu", "te", "so", "li", "na", "ve", "du")
          for b in ("rn", "lt", "nd", "sk", "mp", "ng", "st", "rd", "lm", "xt")]  # fmt: skip


def line_text(i: int, tag: str = "alpha", kstride: int = 13) -> str:
    """Five-word nonsense sentence; different lines share (almost) no words."""
    shift = sum(map(ord, tag)) % 100
    return " ".join(_VOCAB[(shift + 7 * i + kstride * k) % 100] for k in range(5))


def dense_episode(
    number: int, n_lines: int = 20, step: float = 4.0, words: str = "alpha"
) -> Episode:
    """Dialogue every `step` seconds, each 2.5s long, distinct text per line."""
    specs: list[Spec] = [
        (
            D,
            i * step,
            i * step + 2.5,
            line_text(i, words, 13 + 4 * (number - 1)),
            "ANA" if i % 2 else "BEN",
        )
        for i in range(n_lines)
    ]
    return make_episode(number, specs)
