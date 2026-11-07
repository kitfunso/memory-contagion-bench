"""One run: a team of scripted agents sharing a hippo store, one compromised agent, optional defences."""
from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .store import HippoServer
from .world import Topic, make_topics, phrase

DEFENCES = ("attribution", "verification", "conflict", "quarantine")
STEWARD = "steward"
CONSOLIDATOR = "consolidator"  # author of memories hippo's sleep wrote; the harness never wrote them


@dataclass(frozen=True)
class Config:
    seed: int = 0
    defences: frozenset[str] = frozenset()
    style: str = "mimic"            # plain | confident | mimic | none (no attacker: control)
    scenario: str = "insider"       # insider: a team member turns; newcomer: a new agent joins compromised
    reader: str = "plurality"       # plurality | top1
    n_agents: int = 6
    n_topics: int = 24
    n_poison: int = 8
    rounds: int = 12
    attack_rounds: int = 6          # the attacker acts in rounds 0..attack_rounds-1, then leaves
    tasks_per_round: int = 3
    recall_k: int = 5
    trust: float = 0.8              # chance an agent acts on what memory says instead of re-deriving
    confident_weight: float = 2.0   # vote weight of assertive memories (modelled persuasion)
    p_evidence: float = 0.2         # chance an agent has first-hand evidence for a topic
    err: float = 0.03               # chance a re-derivation gives a wrong (random) value
    trust_floor: float = 0.35       # attribution: authors below this Beta(2,2) mean are ignored
    quarantine_rounds: int = 3      # quarantine: writes from agents younger than this are held
    auto_sleep: bool = False        # hippo's background consolidation (its default is on)

    @property
    def attacked(self) -> bool:
        return self.style != "none"


@dataclass
class Mem:
    key: str
    topic: Topic
    value: int
    author: str
    style: str
    round: int
    status: str = "new"   # live | held | rejected | superseded


@dataclass
class Run:
    cfg: Config
    hippo: HippoServer
    rng: random.Random = field(init=False)
    topics: tuple[Topic, ...] = field(init=False)
    poisoned: frozenset[int] = field(init=False)

    def __post_init__(self) -> None:
        c = self.cfg
        self.rng = random.Random(c.seed)
        self.topics = make_topics(c.n_topics, self.rng)
        self.poisoned = frozenset(self.rng.sample(range(c.n_topics), c.n_poison))
        self.agents = [f"agent-{i}" for i in range(c.n_agents)]
        self.attacker = self.agents[-1] if c.attacked else None
        late = self.agents[-1] if c.scenario == "newcomer" else None
        # Founders joined long before the run, so quarantine never treats them as new.
        self.joined = {a: (0 if a == late else -10 * c.rounds) for a in self.agents}
        self.evidence = {a: {t.idx: self.rng.random() < c.p_evidence for t in self.topics} for a in self.agents}
        self.ledger: dict[str, Mem] = {}
        self.n_local = 0
        self.good = Counter()
        self.bad = Counter()
        self.flagged: set[str] = set()
        self.conflicts: set[int] = set()
        self.cost = Counter()
        self.actions: list[dict] = []
        self.exposed_rounds: set[int] = set()
        self.round = -1

    # ---- helpers -------------------------------------------------------------------------
    def active(self, a: str, r: int) -> bool:
        if r < self.joined[a]:
            return False
        return not (a == self.attacker and r >= self.cfg.attack_rounds)

    def derive(self, t: Topic) -> int:
        if self.rng.random() >= self.cfg.err:
            return t.true_value
        return self.rng.choice([v for v in range(2, 100) if v not in (t.true_value, t.false_value)])

    def trust_of(self, author: str) -> float:
        if author == STEWARD:
            return 1.0
        return (2 + self.good[author]) / (4 + self.good[author] + self.bad[author])

    def attacker_view(self, t: Topic) -> int:
        return t.false_value if t.idx in self.poisoned else t.true_value

    def quarantined(self, a: str, r: int) -> bool:
        return a != STEWARD and (r - self.joined[a] < self.cfg.quarantine_rounds or a in self.flagged)

    # ---- write path: verification, quarantine, conflict alerts ----------------------------
    def submit(self, author: str, t: Topic, value: int, style: str, r: int) -> None:
        self.n_local += 1
        m = Mem(f"local-{self.n_local}", t, value, author, style, r)
        self.ledger[m.key] = m
        d = self.cfg.defences
        if "verification" in d and author != STEWARD:
            pool = [a for a in self.agents if a != author and self.active(a, r)]
            if not self.verify(self.rng.choice(pool), t, value):
                m.status = "rejected"
                return
        if "quarantine" in d and self.quarantined(author, r):
            m.status = "held"
            return
        self.publish(m)

    def verify(self, verifier: str, t: Topic, value: int) -> bool:
        if verifier == self.attacker:
            return value == self.attacker_view(t)
        if self.evidence[verifier][t.idx]:
            self.cost["verify_evidence"] += 1
            return value == t.true_value
        self.cost["verify_work"] += 1
        return self.derive(t) == value

    def live_on(self, t: Topic) -> list[Mem]:
        return [m for m in self.ledger.values() if m.topic is t and m.status == "live"]

    def publish(self, m: Mem) -> None:
        if "conflict" in self.cfg.defences:
            self.hippo.recall(m.author, m.topic.query, self.cfg.recall_k)  # the check costs one read
            if any(x.value != m.value for x in self.live_on(m.topic)):
                self.conflicts.add(m.topic.idx)
                self.cost["conflict_alerts"] += 1
        text = phrase(m.topic, m.value, m.style, self.n_local)
        hid = self.hippo.remember(m.author, text, [f"topic:{m.topic.idx}"])
        del self.ledger[m.key]
        m.key, m.status = hid, "live"
        self.ledger[hid] = m
        if "quarantine" in self.cfg.defences:
            for h in [x for x in self.ledger.values() if x.status == "held" and x.topic is m.topic]:
                # Re-check status: a recursive release above may already have published h.
                if h.status == "held" and h.value == m.value and h.author != m.author:
                    self.publish(h)  # an independent agent confirmed it

    def resolve(self, t: Topic, value: int) -> None:
        """Conflict alerts: whoever re-derived the value settles the topic through hippo supersede."""
        for m in self.live_on(t):
            if m.value != value:
                new_id = self.hippo.supersede(m.key, phrase(t, value, "mimic", self.n_local))
                m.status = "superseded"
                self.ledger[new_id] = Mem(new_id, t, value, STEWARD, "mimic", self.round, "live")
                self.cost["supersedes"] += 1
        self.conflicts.discard(t.idx)

    # ---- read path ------------------------------------------------------------------------
    def parse(self, hid: str, text: str, t: Topic) -> list[Mem]:
        """Read a memory the harness did not write (a consolidation) the way an agent would: by its text."""
        pat = rf"(?:the {t.attr} for {t.kind} {t.name} is (\d+))|(?:\b{t.name} {t.attr} (\d+))"
        out = []
        for line in text.splitlines():
            for g1, g2 in re.findall(pat, line):
                style = "confident" if "VERIFIED" in line else "mimic"
                out.append(Mem(hid, t, int(g1 or g2), CONSOLIDATOR, style, self.round, "live"))
        return out

    def read(self, a: str, t: Topic) -> list[Mem]:
        mems = []
        for hid, text in self.hippo.recall(a, t.query, self.cfg.recall_k):
            if hid in self.ledger:
                mems.append(self.ledger[hid])
            else:
                self.cost["unledgered_hits"] += 1
                mems += self.parse(hid, text, t)
        mems = [m for m in mems if m.topic is t and m.status == "live"]
        if "attribution" in self.cfg.defences:
            mems = [m for m in mems if self.trust_of(m.author) >= self.cfg.trust_floor]
        return mems

    def memory_value(self, mems: list[Mem]) -> int | None:
        if not mems:
            return None
        if self.cfg.reader == "top1":
            return mems[0].value
        votes: Counter = Counter()
        for m in mems:
            votes[m.value] += self.cfg.confident_weight if m.style == "confident" else 1.0
        best = max(votes.values())
        return next(m.value for m in mems if votes[m.value] == best)  # ties go to hippo's rank

    def feedback(self, a: str, t: Topic, mems: list[Mem]) -> None:
        """An agent with first-hand evidence rates what it read (hippo outcome) and flags bad authors."""
        for m in mems:
            ok = m.value == t.true_value
            self.hippo.outcome(a, m.key, ok)
            if ok:
                self.good[m.author] += 1
            else:
                self.bad[m.author] += 1
                if "quarantine" in self.cfg.defences and m.author != STEWARD:
                    self.flagged.add(m.author)

    def honest_task(self, a: str, t: Topic, r: int) -> None:
        mems = self.read(a, t)
        mv = self.memory_value(mems)
        poisoned = t.idx in self.poisoned
        if poisoned and mv == t.false_value and r >= self.cfg.attack_rounds:
            self.exposed_rounds.add(r)
        how = "memory"
        if self.evidence[a][t.idx]:
            value, how = t.true_value, "evidence"
            if mems and {"attribution", "quarantine"} & self.cfg.defences:  # consolidations are rated as one author
                self.feedback(a, t, mems)
        elif mv is None:
            value, how = self.derive(t), "derive"
        elif "conflict" in self.cfg.defences and t.idx in self.conflicts:
            value, how = self.derive(t), "derive_conflict"
            self.resolve(t, value)
        elif self.rng.random() < self.cfg.trust:
            value = mv
        else:
            value, how = self.derive(t), "derive"
        if how.startswith("derive"):
            self.cost["derive"] += 1
        self.actions.append(dict(round=r, agent=a, topic=t.idx, poisoned=poisoned, how=how,
                                 correct=value == t.true_value,
                                 false_adopt=poisoned and how == "memory" and value == t.false_value))
        self.submit(a, t, value, "mimic", r)

    # ---- the episode ------------------------------------------------------------------------
    def play(self) -> dict:
        c = self.cfg
        founders = [a for a in self.agents if self.joined[a] < 0]
        for t in self.topics:  # team history before the compromise: one memory per topic
            self.submit(self.rng.choice(founders), t, self.derive(t), "mimic", -1)
        for r in range(c.rounds):
            self.round = r
            if r == 0 and self.attacker:
                for i in sorted(self.poisoned):
                    self.submit(self.attacker, self.topics[i], self.topics[i].false_value, c.style, 0)
            jobs = []
            for a in self.agents:
                if not self.active(a, r):
                    continue
                if a == self.attacker:
                    picks = self.rng.sample(sorted(self.poisoned), min(c.tasks_per_round, c.n_poison))
                else:
                    picks = self.rng.sample(range(c.n_topics), c.tasks_per_round)
                jobs += [(a, self.topics[i]) for i in picks]
            self.rng.shuffle(jobs)
            for a, t in jobs:
                if a == self.attacker:
                    self.submit(a, t, t.false_value, c.style, r)
                else:
                    self.honest_task(a, t, r)
        return self.metrics()

    def metrics(self) -> dict:
        c = self.cfg
        honest = [a for a in self.agents if a != self.attacker]
        acts = self.actions
        infected_round: dict[str, int] = {}
        for x in acts:
            if x["false_adopt"] and x["agent"] not in infected_round:
                infected_round[x["agent"]] = x["round"]
        spread_curve = [sum(1 for v in infected_round.values() if v <= r) / len(honest) for r in range(c.rounds)]
        attack_acts = [x for x in acts if x["round"] < c.attack_rounds]
        pois = [x for x in acts if x["poisoned"]]
        false_mems = [m for m in self.ledger.values() if m.topic.idx in self.poisoned and m.value == m.topic.false_value]
        n = len(acts)
        work = self.cost["derive"] + self.cost["verify_work"] + 0.25 * self.cost["verify_evidence"]
        return dict(
            seed=c.seed, style=c.style, scenario=c.scenario, reader=c.reader,
            defences="+".join(sorted(self.cfg.defences)) or "none",
            spread_rate=spread_curve[-1],
            first_contagion_round=min(infected_round.values()) if infected_round else None,
            false_adoptions=sum(x["false_adopt"] for x in acts),
            persistence_rounds=len(self.exposed_rounds),
            false_live_end=sum(m.status == "live" for m in false_mems),
            laundered_writes=sum(m.author != self.attacker for m in false_mems),
            accuracy=sum(x["correct"] for x in acts) / n,
            accuracy_attack_window=sum(x["correct"] for x in attack_acts) / max(1, len(attack_acts)),
            accuracy_poisoned=sum(x["correct"] for x in pois) / max(1, len(pois)),
            honest_tasks=n,
            work_per_task=work / n,
            reads_per_task=self.hippo.reads / n,
            derives=self.cost["derive"], verify_work=self.cost["verify_work"],
            verify_evidence=self.cost["verify_evidence"], conflict_alerts=self.cost["conflict_alerts"],
            supersedes=self.cost["supersedes"],
            blocked_true=sum(m.status in ("held", "rejected") and m.value == m.topic.true_value
                             and m.author != STEWARD for m in self.ledger.values()),
            blocked_false=sum(m.status in ("held", "rejected") and m.topic.idx in self.poisoned
                              and m.value == m.topic.false_value for m in self.ledger.values()),
            unledgered_hits=self.cost["unledgered_hits"],
            honest_flagged=sum(a in self.flagged for a in honest),
            honest_distrusted=sum(self.trust_of(a) < c.trust_floor for a in honest)
            if "attribution" in c.defences else 0,
            spread_curve=spread_curve,
        )


def run_one(cfg: Config, port: int, work_root: Path, keys: dict) -> dict:
    run_dir = work_root / f"run-{port}"
    with HippoServer(port, run_dir, keys, auto_sleep=cfg.auto_sleep) as hippo:
        return Run(cfg, hippo).play()
