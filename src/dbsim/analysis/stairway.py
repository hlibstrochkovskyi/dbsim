"""Blocking-time stairway visualisation & minimum headway (M3.2).

The **blocking-time stairway** plots each block's blocking interval as a bar in
the (time, distance) plane; as a train advances, the bars step up — the staircase.
Two trains following on the same track must keep their stairways from overlapping;
the **minimum headway** is the smallest time offset at which the follower's
stairway just touches the leader's (set by the block with the longest blocking
time — the critical block).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbsim.engine.blocking import BlockingInterval, BlockTraversal


@dataclass(frozen=True, slots=True)
class StairwayTrain:
    """One train's contribution to a stairway: its trajectory and blocking bars."""

    label: str
    traversal: tuple[BlockTraversal, ...]
    blocking: tuple[BlockingInterval, ...]


def minimum_headway_s(leader: list[BlockingInterval], follower: list[BlockingInterval]) -> float:
    """Minimum time the follower must trail the leader to avoid any block overlap.

    Both blocking lists are computed from the same ``start_time``; the headway is
    the largest ``leader.end − follower.start`` across shared blocks (the critical
    block sets it).
    """
    follower_start = {b.block_id: b.start_s for b in follower}
    return max(
        (b.end_s - follower_start[b.block_id] for b in leader if b.block_id in follower_start),
        default=0.0,
    )


def render_stairway(
    trains: list[StairwayTrain], out_path: Path, *, title: str | None = None
) -> Path:
    """Render a blocking-time stairway (distance vs time) for a train sequence."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    colors = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]
    fig, ax = plt.subplots(figsize=(14, 7))

    for idx, train in enumerate(trains):
        color = colors[idx % len(colors)]
        for bi in train.blocking:
            ax.add_patch(
                Rectangle(
                    (bi.start_s, bi.dist_start_m),
                    bi.end_s - bi.start_s,
                    bi.dist_end_m - bi.dist_start_m,
                    facecolor=color,
                    alpha=0.18,
                    edgecolor=color,
                    linewidth=0.6,
                )
            )
        # The train's front trajectory: a point per block boundary.
        xs = [train.traversal[0].enter_s] + [tr.exit_s for tr in train.traversal]
        ys = [train.traversal[0].dist_start_m] + [tr.dist_end_m for tr in train.traversal]
        ax.plot(xs, ys, color=color, linewidth=1.6, label=train.label)

    # Block boundaries as horizontal grid lines.
    if trains:
        for tr in trains[0].traversal:
            ax.axhline(tr.dist_end_m, color="0.9", linewidth=0.6, zorder=0)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Distance along route (m)")
    ax.set_title(title or "Blocking-time stairway")
    ax.legend(loc="lower right", fontsize=8)
    ax.margins(0.02)
    ax.autoscale_view()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
