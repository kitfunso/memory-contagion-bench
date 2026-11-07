"""Figures and the main table from results/summary.json."""
from __future__ import annotations

import csv
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .grid import RESULTS  # noqa: E402

DEF_ORDER = ("none", "attribution", "verification", "conflict", "quarantine", "all")
DEF_KEY = {"none": "none", "attribution": "attribution", "verification": "verification", "conflict": "conflict",
           "quarantine": "quarantine", "all": "attribution+conflict+quarantine+verification"}
# Fixed categorical order from the validated reference palette; the undefended baseline is neutral ink.
COLOR = {"none": "#52514e", "attribution": "#2a78d6", "verification": "#eb6834", "conflict": "#1baf7a",
         "quarantine": "#eda100", "all": "#e87ba4"}
MARK = {"none": "o", "attribution": "s", "verification": "^", "conflict": "D", "quarantine": "v", "all": "P"}
STYLE_MARK = {"plain": "o", "confident": "^", "mimic": "s"}
FIG = RESULTS / "figures"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#e4e3df", "grid.linewidth": 0.6, "axes.axisbelow": True,
                     "axes.edgecolor": "#8a8984", "figure.facecolor": "#ffffff", "savefig.dpi": 200})


def cell(summary, block="main", scenario="insider", reader="plurality", n=6, style="mimic", d="none"):
    for r in summary:
        if (r["block"], r["scenario"], r["reader"], r["n_agents"], r["style"], r["defences"]) == \
                (block, scenario, reader, n, style, DEF_KEY[d]):
            return r
    raise KeyError((block, scenario, reader, n, style, d))


def fig_curves(s) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.5), sharey=True)
    for ax, style in zip(axes, ("plain", "confident", "mimic")):
        ax.axvspan(-0.5, 5.5, color="#f3f2ee", zorder=0)
        for d in DEF_ORDER:
            y = cell(s, style=style, d=d)["spread_curve_mean"]
            ax.plot(range(len(y)), y, color=COLOR[d], marker=MARK[d], markersize=4, linewidth=1.6, label=d)
        ax.set_title(f"attacker style: {style}")
        ax.set_ylim(-0.02, 1.0)
    fig.supxlabel("round (attacker active in the shaded rounds 0-5, then leaves)", fontsize=9)
    axes[0].set_ylabel("share of honest agents\nthat acted on a false memory")
    axes[2].legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(1.0, 1.0),
                   title="verification, conflict\nand all stay at 0", title_fontsize=6.5)
    fig.tight_layout()
    fig.savefig(FIG / "fig1_spread_over_time.png", bbox_inches="tight")


def fig_final(s) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6), sharey=True)
    for ax, scen in zip(axes, ("insider", "newcomer")):
        for i, d in enumerate(DEF_ORDER):
            for j, style in enumerate(("plain", "confident", "mimic")):
                c = cell(s, scenario=scen, style=style, d=d)
                y = i + (j - 1) * 0.22
                ax.plot([c["spread_rate_min"], c["spread_rate_max"]], [y, y], color=COLOR[d], linewidth=1.2)
                ax.plot(c["spread_rate_mean"], y, marker=STYLE_MARK[style], color=COLOR[d], markersize=5,
                        markeredgecolor="white", markeredgewidth=0.6, linestyle="none")
        ax.set_yticks(range(len(DEF_ORDER)), DEF_ORDER)
        ax.invert_yaxis()
        ax.set_xlim(-0.03, 1.03)
        ax.set_xlabel("final share of honest agents infected")
        ax.set_title(f"{scen} attacker")
    handles = [plt.Line2D([], [], marker=m, color="#52514e", linestyle="none", label=st) for st, m in STYLE_MARK.items()]
    fig.legend(handles=handles, frameon=False, fontsize=7, title="attacker style (marker = mean of 10 seeds; line = min to max)",
               title_fontsize=7, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    fig.savefig(FIG / "fig2_final_spread.png", bbox_inches="tight")


def fig_tradeoff(s) -> None:
    fig, ax = plt.subplots(figsize=(4.6, 3.0))
    for d in DEF_ORDER:
        a, c = cell(s, style="mimic", d=d), cell(s, style="none", d=d)
        ax.plot([c["work_per_task_mean"], a["work_per_task_mean"]], [c["accuracy_mean"], a["accuracy_mean"]],
                color=COLOR[d], linewidth=0.8, linestyle=":")
        ax.plot(a["work_per_task_mean"], a["accuracy_mean"], marker=MARK[d], color=COLOR[d], markersize=7,
                linestyle="none", label=d)
        ax.plot(c["work_per_task_mean"], c["accuracy_mean"], marker=MARK[d], markerfacecolor="white",
                color=COLOR[d], markersize=7, linestyle="none")
        off = {"verification": (-10, -13), "all": (2, -13), "conflict": (6, 4), "quarantine": (7, -3),
               "attribution": (7, -3), "none": (7, -3)}[d]
        ax.annotate(d, (a["work_per_task_mean"], a["accuracy_mean"]), xytext=off, textcoords="offset points",
                    fontsize=7, color="#0b0b0b")
    ax.set_xlabel("work per honest task (re-derivations + checks)")
    ax.set_ylabel("honest task accuracy")
    ax.set_title("filled: mimic attack (insider)   hollow: no attacker", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "fig3_accuracy_cost.png", bbox_inches="tight")


def fig_sensitivity(s) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.4))
    ax = axes[0]
    for d in ("none", "all"):
        xs = (4, 6, 10)
        ys = [cell(s, block="team_size", n=n, d=d)["spread_rate_mean"] for n in xs]
        lo = [cell(s, block="team_size", n=n, d=d)["spread_rate_min"] for n in xs]
        hi = [cell(s, block="team_size", n=n, d=d)["spread_rate_max"] for n in xs]
        ax.fill_between(xs, lo, hi, color=COLOR[d], alpha=0.15, linewidth=0)
        ax.plot(xs, ys, color=COLOR[d], marker=MARK[d], linewidth=1.6, label=d)
    ax.set_xticks((4, 6, 10))
    ax.set_xlabel("team size (one compromised agent)")
    ax.set_ylabel("final share infected")
    ax.set_ylim(-0.02, 1.0)
    ax.set_title("team size, mimic attacker")
    ax.legend(frameon=False, fontsize=7)
    ax = axes[1]
    for k, reader in enumerate(("plurality", "top1")):
        vals = [cell(s, block="main" if reader == "plurality" else "reader_top1", reader=reader, style=st)
                for st in ("plain", "confident", "mimic")]
        xs = [i + (k - 0.5) * 0.36 for i in range(3)]
        ax.bar(xs, [v["spread_rate_mean"] for v in vals], width=0.34, color=("#2a78d6", "#eb6834")[k],
               label=f"reader: {reader}", edgecolor="white", linewidth=1)
        ax.errorbar(xs, [v["spread_rate_mean"] for v in vals],
                    yerr=[[v["spread_rate_mean"] - v["spread_rate_min"] for v in vals],
                          [v["spread_rate_max"] - v["spread_rate_mean"] for v in vals]],
                    fmt="none", ecolor="#52514e", elinewidth=0.8, capsize=2)
    ax.set_xticks(range(3), ("plain", "confident", "mimic"))
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("final share infected")
    ax.set_title("how agents read memory, no defence", pad=16)
    ax.legend(frameon=False, fontsize=7, loc="lower center", bbox_to_anchor=(0.5, 0.98), ncol=2)
    fig.tight_layout()
    fig.savefig(FIG / "fig4_sensitivity.png", bbox_inches="tight")


def table(s) -> list[dict]:
    rows = []
    for scen in ("insider", "newcomer"):
        for d in DEF_ORDER:
            a, c = cell(s, scenario=scen, style="mimic", d=d), cell(s, scenario=scen, style="none", d=d)
            rows.append(dict(
                scenario=scen, defence=d,
                spread=f"{a['spread_rate_mean']:.2f} [{a['spread_rate_min']:.1f}, {a['spread_rate_max']:.1f}]",
                first_round=(f"{a['first_contagion_round_mean']:.1f} ({a['first_contagion_n']}/{a['seeds']})"
                             if a["first_contagion_n"] else f"never (0/{a['seeds']})"),
                persistence=f"{a['persistence_rounds_mean']:.1f}",
                false_live_end=f"{a['false_live_end_mean']:.1f}",
                laundered=f"{a['laundered_writes_mean']:.1f}",
                accuracy=f"{a['accuracy_mean']:.3f}",
                work_attack=f"{a['work_per_task_mean']:.2f}",
                work_no_attack=f"{c['work_per_task_mean']:.2f}",
                accuracy_no_attack=f"{c['accuracy_mean']:.3f}",
                blocked_true_no_attack=f"{c['blocked_true_mean']:.1f}",
                reads_per_task=f"{a['reads_per_task_mean']:.2f}"))
    with open(RESULTS / "table_main.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (RESULTS / "table_main.json").write_text(json.dumps(rows, indent=1))
    return rows


def main() -> None:
    s = json.loads((RESULTS / "summary.json").read_text())
    FIG.mkdir(exist_ok=True)
    fig_curves(s)
    fig_final(s)
    fig_tradeoff(s)
    fig_sensitivity(s)
    for r in table(s):
        print(json.dumps(r))


if __name__ == "__main__":
    main()
