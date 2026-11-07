"""Run the experiment grid in parallel (one hippo server per worker) and write results/."""
from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import statistics
import time
from pathlib import Path

from .sim import Config, run_one
from .store import CACHE, ROOT, build_template

RESULTS = ROOT / "results"
DEFENCE_SETS = {
    "none": frozenset(),
    "attribution": frozenset({"attribution"}),
    "verification": frozenset({"verification"}),
    "conflict": frozenset({"conflict"}),
    "quarantine": frozenset({"quarantine"}),
    "all": frozenset({"attribution", "verification", "conflict", "quarantine"}),
}
METRICS = ("spread_rate", "first_contagion_round", "false_adoptions", "persistence_rounds", "false_live_end",
           "laundered_writes", "accuracy", "accuracy_attack_window", "accuracy_poisoned", "work_per_task",
           "reads_per_task", "conflict_alerts", "supersedes", "blocked_true", "blocked_false",
           "honest_flagged", "honest_distrusted", "unledgered_hits")


def configs(seeds: int) -> list[tuple[str, Config]]:
    out = []
    for seed in range(seeds):
        for dname, d in DEFENCE_SETS.items():
            for scenario in ("insider", "newcomer"):
                for style in ("plain", "confident", "mimic", "none"):
                    out.append(("main", Config(seed=seed, defences=d, style=style, scenario=scenario)))
            for style in ("plain", "confident", "mimic"):
                out.append(("reader_top1", Config(seed=seed, defences=d, style=style, reader="top1")))
        for style in ("plain", "confident", "mimic", "none"):  # hippo as shipped: auto-sleep on
            out.append(("auto_sleep", Config(seed=seed, style=style, auto_sleep=True)))
        for n in (4, 6, 10):
            for dname in ("none", "all"):
                out.append(("team_size", Config(seed=seed, defences=DEFENCE_SETS[dname], style="mimic",
                                                n_agents=n)))
    return out


_PORT = 0


def _init(q: mp.Queue) -> None:
    global _PORT
    _PORT = q.get()


def job_id(block: str, c: Config) -> str:
    return f"{block}|{c.scenario}|{c.reader}|{c.n_agents}|{c.style}|{'+'.join(sorted(c.defences)) or 'none'}|{c.seed}"


def _work(job: tuple[str, Config]) -> dict:
    block, cfg = job
    keys = json.loads((CACHE / "template" / "keys.json").read_text())
    try:
        m = run_one(cfg, _PORT, CACHE / "work", keys)
    except Exception as e:  # report, never kill the pool: a torn-down pool orphans the other servers
        return {"job": job_id(block, cfg), "error": f"{type(e).__name__}: {e}"}
    m.update(block=block, n_agents=cfg.n_agents, job=job_id(block, cfg))
    return m


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["block"], r["scenario"], r["reader"], r["n_agents"], r["style"], r["defences"]), []).append(r)
    out = []
    for (block, scen, reader, n, style, d), rs in sorted(groups.items()):
        row = dict(block=block, scenario=scen, reader=reader, n_agents=n, style=style, defences=d, seeds=len(rs))
        for k in METRICS:
            vals = [r[k] for r in rs if r[k] is not None]
            row[f"{k}_mean"] = statistics.fmean(vals) if vals else None
            row[f"{k}_min"] = min(vals) if vals else None
            row[f"{k}_max"] = max(vals) if vals else None
        row["first_contagion_n"] = sum(r["first_contagion_round"] is not None for r in rs)
        curves = [r["spread_curve"] for r in rs]
        row["spread_curve_mean"] = [statistics.fmean(c[i] for c in curves) for i in range(len(curves[0]))]
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--workers", type=int, default=min(10, os.cpu_count() or 4))
    args = ap.parse_args()
    build_template()
    RESULTS.mkdir(exist_ok=True)
    log = RESULTS / "runs.jsonl"  # one line per finished run, so a crash loses nothing and a rerun resumes
    rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    done = {r["job"] for r in rows}
    jobs = [j for j in configs(args.seeds) if job_id(*j) not in done]
    q: mp.Queue = mp.Queue()
    for i in range(args.workers):
        q.put(7100 + i)
    t0 = time.time()
    with mp.Pool(args.workers, initializer=_init, initargs=(q,)) as pool, open(log, "a") as out:
        for i, m in enumerate(pool.imap_unordered(_work, jobs), 1):
            if "error" in m:
                print(json.dumps(m), flush=True)  # not logged, so a rerun retries it
                continue
            rows.append(m)
            out.write(json.dumps(m) + "\n")
            out.flush()
            if i % 50 == 0 or i == len(jobs):
                print(json.dumps({"done": i, "of": len(jobs), "elapsed_s": round(time.time() - t0)}), flush=True)
    rows.sort(key=lambda r: (r["block"], r["scenario"], r["reader"], r["n_agents"], r["style"], r["defences"], r["seed"]))
    (RESULTS / "runs.json").write_text(json.dumps(rows, indent=1))
    summary = summarise(rows)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1))
    flat = [{k: v for k, v in r.items() if k != "spread_curve"} for r in rows]
    with open(RESULTS / "runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flat[0]))
        w.writeheader()
        w.writerows(flat)
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        cols = [k for k in summary[0] if k != "spread_curve_mean"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(summary)
    print(json.dumps({"new_runs": len(jobs), "runs": len(rows), "cells": len(summary), "elapsed_s": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
