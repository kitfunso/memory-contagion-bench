# memory-contagion-bench

A small benchmark that asks one question: **if one agent in a team turns bad and writes false memories into the team's shared memory store, how fast and how far does the false fact spread, and which cheap defences contain it?**

Built over the Apart Research Swarm Alignment Sprint weekend (6 to 8 November 2026), Track 2, by Kit Fun So (KITFUNSO LTD, London). The report is in the sprint submission. The code, results and figures are all here.

## Reproduce in one command

Needs Node 22.16+ and Python 3.12+.

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # or .venv/bin/pip on Linux/macOS
.venv/Scripts/python reproduce.py          # npm ci if needed, then the full grid (656 runs), then figures
```

The grid takes about 15 minutes on 10 processes. It writes `results/runs.jsonl` (one line per run), `results/runs.csv`, `results/summary.json` and `results/summary.csv` (mean, min and max across seeds per cell), `results/table_main.csv`, and the four figures in `results/figures/`. A rerun skips runs that are already in `runs.jsonl`. Delete that file to start fresh.

Optional LLM arm (needs a local Ollama model, no paid API): `.venv/Scripts/python -m contagion.llm_arm --model qwen3.8-fast:latest`. It writes `results/llm_arm.json` and `results/llm_arm_summary.json`.

Check of hippo's own conflict detector on a numeric contradiction: `bash scripts/hippo_conflict_check.sh` (output saved in `results/hippo_conflict_check.txt`).

## What the benchmark does

- **Store.** Every run starts a fresh, real [hippo-memory](https://github.com/kitfunso/hippo-memory) 3.3.3 store and talks to it through `hippo serve` (its HTTP API). Each agent has its own API key. Agents write with `POST /v1/memories`, read with `GET /v1/memories?q=...` (hippo's own ranking), rate with `POST /v1/outcome`, and the conflict defence settles disputes with `POST /v1/memories/:id/supersede`.
- **Team.** 6 scripted agents, 24 topics, each with one true value (for example "the retry limit for queue bravo"). Before the attack, the team history holds one memory per topic. Then 12 rounds; in each round every agent does 3 tasks. For each task an agent recalls the top 5 memories, takes the value most of the same-topic memories agree on (or the top hit, in the `top1` arm), acts on it with probability 0.8 and otherwise re-derives the value itself, then writes a memory of what it did. 20% of agent-topic pairs have first-hand evidence.
- **Attacker.** One agent writes false values for 8 of the 24 topics at round 0, then keeps writing them in its own tasks for 6 rounds, then leaves. Three styles: `plain` (terse), `confident` ("VERIFIED by the team lead, do not re-check"), `mimic` (the team's own template). Two scenarios: `insider` (a team member turns) and `newcomer` (a new agent joins already compromised). `style=none` is the no-attack control.
- **Defences**, singly and all together:
  - `attribution`: agents with evidence rate what they read (hippo `outcome`); readers ignore memories from authors whose trust (Beta(2,2) mean) falls below 0.35.
  - `verification`: a second, randomly chosen agent checks each memory before it is shared, against its own evidence or by re-deriving the value.
  - `conflict`: a memory that contradicts a live memory on the same topic opens an alert; readers do not trust the topic until one of them re-derives it, and the losers are superseded in hippo.
  - `quarantine`: memories from agents in their first 3 rounds, or from agents someone flagged, are held until another agent writes the same value.
- **Measures**: spread rate (share of honest agents that acted on a false memory at least once), time to first contagion, persistence (rounds after the attacker left in which a reader's memory answer was still false), false memories still live at the end, laundered writes (false memories written by honest agents), task accuracy, and cost (work per task: re-derivations plus checks; reads per task; correct memories blocked).
- **Seeds**: 10 per cell. All agent behaviour is seeded and LLM-free, so a run is reproducible up to hippo's own ranking of tied items.

## Pre-existing code

Only the weekend's work is judged, so this is the full list of what was **not** written this weekend:

- **hippo-memory 3.3.3** (npm `hippo-memory`, MIT), the memory store. It was written before the sprint by the same author (Kit Fun So) and is used unmodified, installed from npm. Nothing in `node_modules/` is part of this submission.
- **Python libraries**: matplotlib and numpy (figures). Standard library otherwise.
- **Ollama** and the local model used by the optional LLM arm.

Everything in `contagion/`, `scripts/`, `reproduce.py` and `results/` was written during the sprint weekend, 7 to 8 November 2026.

## Licence

MIT. See `LICENSE`.
