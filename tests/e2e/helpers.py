"""Writes a synthetic show (invented content) in the real screenplay file format."""

from __future__ import annotations

from pathlib import Path

from tests.candidate.conftest import line_text

SPEAKERS = [("ANA", "Furious, Loud"), ("BEN", "Cold"), ("CAL", "Trembling, Pleading")]


def _ts(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:05.2f}"


def episode_text(n: int, dur: float = 110.0) -> str:
    lines = [
        f"EPISODE NUMBER: Episode {n}",
        f"EPISODE LOGLINE: Synthetic episode {n} where a baker faces a rival.",
        f"CONTINUITY NOTES: {'First episode.' if n == 1 else 'Picks up directly from the previous episode.'}",
        "",
        "--- SCRIPT START ---",
        "",
    ]
    t, i = 0.0, 0
    scene_len = dur / 2
    for sc in (1, 2):
        lines += [f"SCENE {sc:02d}", f"Scene heading: INT. BAKERY {sc} – DAY",
                  f"Scene start: {_ts((sc - 1) * scene_len)}", f"Scene end: {_ts(sc * scene_len)}", "",
                  "Actions and dialogue:", ""]  # fmt: skip
        while t < sc * scene_len - 4:
            who, tags = SPEAKERS[i % 3]
            text = line_text(i, f"show{n}", 13 + 4 * (n - 1))
            if i % 4 == 0:
                text += " You lied to me!"
            lines += [
                "**CAMERA:**",
                f"Start: ({_ts(t)}) | End: ({_ts(t + 3.5)})",
                "Close-up on the speaker.",
                "",
            ]
            lines += [
                f"**{who} ({tags}):**",
                f"Start: ({_ts(t + 0.2)}) | End: ({_ts(t + 2.7)})",
                f'"{text}"',
                "",
            ]
            if i % 5 == 4:
                lines += ["**ACTION:**", f"Start: ({_ts(t + 2.8)}) | End: ({_ts(t + 3.4)})",
                          "He slaps her across the face.", ""]  # fmt: skip
            t += 3.5
            i += 1
    lines += [
        "**CAL (Shocked):**", f"Start: ({_ts(dur - 6)}) | End: ({_ts(dur - 3.5)})", '"Who are you?"', "",
        "**SOUND FX:**", f"Start: ({_ts(dur - 3.4)}) | End: ({_ts(dur - 1)})", "Ominous cliffhanger sting.", "",
        "**CAMERA:**", f"Start: ({_ts(dur - 3.4)}) | End: ({_ts(dur)})", "Hold, then an abrupt cut to black.", "",
        "--- SCRIPT END ---",
    ]  # fmt: skip
    return "\n".join(lines)


def write_show(root: Path, n_eps: int = 6, name: str = "Synthetic Bakery Show") -> Path:
    d = root / name
    d.mkdir(parents=True)
    for n in range(1, n_eps + 1):
        (d / f"episode-{n:03d}-Episode {9100 + n}.txt").write_text(episode_text(n))
    return d
