"""Render the JSON results from the tests/ suites into PNG charts."""
# pylint: disable=wrong-import-position, too-many-arguments, too-many-positional-arguments

import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results", "metrics")

_BLUE     = "#2a78d6"
_GOOD     = "#0ca30c"
_CRITICAL = "#d03b3b"
_INK      = "#0b0b0b"
_MUTED    = "#898781"
_GRID     = "#e1e0d9"


def _load(path: str):
    """Load a JSON results file, or None if it hasn't been generated yet."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def _style_axis(ax) -> None:
    """Apply the shared light/thin chart style to one subplot."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_MUTED)
    ax.spines["bottom"].set_color(_MUTED)
    ax.tick_params(colors=_MUTED)
    ax.yaxis.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.title.set_color(_INK)


def _bar(ax, labels, values, title: str, ylabel: str, color) -> None:
    """Simple vertical bar chart with value labels on top of each bar."""
    bars = ax.bar(labels, values, color=color, width=0.5)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    _style_axis(ax)
    for b in bars:
        h = b.get_height()
        ax.annotate(f"{h:.1f}", (b.get_x() + b.get_width() / 2, h),
                    textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=9, color=_INK)


def _barh(ax, labels, values, title: str, colors) -> None:
    """Simple horizontal bar chart with value labels beside each bar."""
    bars = ax.barh(labels, values, color=colors, height=0.5)
    ax.set_title(title)
    ax.invert_yaxis()
    _style_axis(ax)
    for b in bars:
        w = b.get_width()
        ax.annotate(f"{w:.0f}", (w, b.get_y() + b.get_height() / 2),
                    textcoords="offset points", xytext=(4, 0),
                    va="center", fontsize=9, color=_INK)


def _save(fig, filename: str) -> None:
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    path = os.path.join(_RESULTS_DIR, filename)
    fig.savefig(path, dpi=120, facecolor="#fcfcfb")
    plt.close(fig)
    print(f"[REPORT] Saved {path}")


def chart_reliability(data: dict) -> None:
    """Reconnect success rate and post-reconnect message loss, side by side."""
    reconnect = data.get("reconnect", {})
    msg       = data.get("msg_after_reconnect", {})

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    _bar(axes[0], ["Success rate"], [reconnect.get("success_rate_pct", 0)],
         "Reconnect success rate", "%", _GOOD)
    _bar(axes[1], ["Loss rate"], [msg.get("loss_rate_pct", 0)],
         "Message loss after reconnect", "%", _CRITICAL)
    fig.tight_layout()
    _save(fig, "reliability_report.png")


def chart_security(data: dict) -> None:
    """TOFU transition pass/fail counts and replay-attack attempted vs accepted."""
    tofu   = data.get("tofu_transitions", {})
    replay = data.get("replay_attack", {})

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    _barh(axes[0], ["Passed", "Failed"],
          [tofu.get("passed", 0), tofu.get("failed", 0)],
          "TOFU transitions", [_GOOD, _CRITICAL])
    _bar(axes[1], ["Attempted", "Accepted"],
         [replay.get("replays_attempted", 0), replay.get("replays_accepted", 0)],
         "Replay attack", "count", _BLUE)
    fig.tight_layout()
    _save(fig, "security_report.png")


def chart_file_transfer(data: dict) -> None:
    """Throughput vs file size (line) and per-size latency (bar)."""
    scaling = data.get("throughput_scaling", {})
    sizes   = sorted(scaling.keys(), key=int)
    throughputs = [scaling[s].get("avg_throughput_kb_s", 0) for s in sizes]
    latencies   = [(scaling[s].get("min_s", 0) + scaling[s].get("max_s", 0)) / 2
                   for s in sizes]

    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    axes[0].plot(sizes, throughputs, marker="o", color=_BLUE, linewidth=2)
    axes[0].set_title("Throughput vs file size")
    axes[0].set_xlabel("File size (KB)")
    axes[0].set_ylabel("KB/s")
    _style_axis(axes[0])

    _bar(axes[1], sizes, latencies, "Latency per size", "seconds", _BLUE)
    fig.tight_layout()
    _save(fig, "file_transfer_report.png")


if __name__ == "__main__":
    reliability = _load(os.path.join(_RESULTS_DIR, "reliability_test_results.json"))
    if reliability is not None:
        chart_reliability(reliability)

    security = _load(os.path.join(_RESULTS_DIR, "security_test_results.json"))
    if security is not None:
        chart_security(security)

    file_transfer = _load(os.path.join(_RESULTS_DIR, "file_transfer_test_results.json"))
    if file_transfer is not None:
        chart_file_transfer(file_transfer)
