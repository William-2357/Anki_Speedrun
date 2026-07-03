# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""Pluggable model backends for the LLM drafter/critic path (M1).

Backends:

- ``mock`` - deterministic canned responses (tests + offline pipeline runs).
- ``claude-cli`` - shells out to ``claude -p --model <m>`` reading the prompt
  from stdin (the host has /opt/homebrew/bin/claude).
- ``openai-compatible`` - POSTs to ``$OPENAI_BASE_URL/chat/completions`` with
  ``$OPENAI_API_KEY`` (stdlib urllib; no third-party client).

INDEPENDENCE NOTE [R23]: the drafter and critic must be different models, or
at minimum different model configurations (model id / system framing /
temperature). The default claude-cli pairing uses two different model ids of
the SAME family (drafter=sonnet, critic=haiku): a same-family critic shares
training-data blind spots with its drafter, which WEAKENS the independence of
the check. Cross-family pairing (e.g. an OpenAI-compatible critic against a
Claude drafter) is strictly better. The residual automation-bias risk of an
all-machine gate chain is explicitly accepted by owner decision 2026-07-02;
the compensating runtime control is that ungraded generated items never feed
readiness [R24].

MULTI-SAMPLE CONSENSUS: for each drafted item, ``k`` independent solver
samples (fresh calls, distinct sample indices) must all pick the labelled
correct answer, AND the critic must approve, before the item is accepted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from aig import prompts

CLAUDE_BIN = "/opt/homebrew/bin/claude"
CALL_TIMEOUT_SECONDS = 120


class Backend(Protocol):
    name: str

    def complete(self, prompt: str, *, sample_index: int = 0) -> str: ...


def parse_json_reply(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model reply (fence-tolerant)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


# ---------------------------------------------------------------------------
# Mock backend (deterministic, offline)
# ---------------------------------------------------------------------------

# Canned drafts keyed by cluster. Values are functions of the sample index so
# repeated draws stay deterministic but distinguishable. The mock drafter's
# items are internally consistent, so the mock solver (which recomputes the
# arithmetic the same canned way) and the mock critic agree.
_MOCK_DRAFTS: dict[str, dict[str, Any]] = {
    "fi::duration": {
        "stem": (
            "A semiannual-pay bond has a Macaulay duration of 6.30 years at "
            "a quoted annual yield of 4.00%. Its modified duration is "
            "closest to:"
        ),
        "choices": {"A": "6.18 years", "B": "6.30 years", "C": "6.05 years"},
        "correct": "A",
        "rationale": ("ModDur = MacDur / (1 + y/k) = 6.30 / 1.02 = 6.18 years."),
        "distractor_rationales": {
            "B": "Incorrect - quotes the Macaulay figure unchanged.",
            "C": "Incorrect - divides by (1 + y) using the full annual yield.",
        },
        "misconceptions": {
            "B": "duration.modified_vs_macaulay",
            "C": "duration.compounding_confusion",
        },
        "title": "Mock: modified duration conversion",
    },
    "qm::tvm": {
        "stem": (
            "$1,000 is deposited today at a stated annual rate of 6.00% "
            "compounded semiannually. The balance after 5 years is closest "
            "to:"
        ),
        "choices": {"A": "$1,343.92", "B": "$1,338.23", "C": "$1,300.00"},
        "correct": "A",
        "rationale": "FV = 1000 x (1.03)^10 = $1,343.92.",
        "distractor_rationales": {
            "B": "Incorrect - compounds annually at 6% (rate-per-period confusion).",
            "C": "Incorrect - applies simple interest.",
        },
        "misconceptions": {
            "B": "tvm.rate_per_period",
            "C": "tvm.simple_vs_compound",
        },
        "title": "Mock: FV with semiannual compounding",
    },
    "fsa::inventory": {
        "stem": (
            "Under rising prices, a firm reports LIFO inventory of $500,000 "
            "and a LIFO reserve of $80,000. Inventory restated to FIFO is "
            "closest to:"
        ),
        "choices": {"A": "$420,000", "B": "$580,000", "C": "$500,000"},
        "correct": "B",
        "rationale": "FIFO inventory = LIFO inventory + LIFO reserve = $580,000.",
        "distractor_rationales": {
            "A": "Incorrect - subtracts the reserve (wrong direction).",
            "C": "Incorrect - ignores the reserve entirely.",
        },
        "misconceptions": {
            "A": "inventory.lifo_reserve_direction",
            "C": "inventory.lifo_reserve_direction",
        },
        "title": "Mock: LIFO reserve restatement",
    },
}


@dataclass
class MockBackend:
    """Deterministic canned backend for tests and offline runs.

    ``failure_mode`` lets tests exercise the gate chain:
    - "wrong_solver": the solver picks a deterministic WRONG letter.
    - "split_solver": solver samples disagree with each other.
    - "reject_critic": the critic rejects everything.
    """

    role: str = "generic"
    failure_mode: str = ""
    name: str = "mock"

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        if "Return ONLY a JSON object" not in prompt:
            return "OK"
        if '"verdict"' in prompt:  # critic
            if self.failure_mode == "reject_critic":
                return json.dumps(
                    {"verdict": "reject", "reasons": ["mock adversarial reject"]}
                )
            return json.dumps({"verdict": "accept", "reasons": []})
        if '"answer"' in prompt:  # solver
            return json.dumps({"answer": self._solve(prompt, sample_index)})
        # drafter
        cluster = "fi::duration"
        for c in _MOCK_DRAFTS:
            if c in prompt:
                cluster = c
                break
        return json.dumps(_MOCK_DRAFTS[cluster])

    def _solve(self, prompt: str, sample_index: int) -> str:
        letters = ["A", "B", "C"]
        correct = ""
        for draft in _MOCK_DRAFTS.values():
            if draft["stem"] in prompt:
                correct = draft["correct"]
                break
        if not correct:
            # Unknown item: deterministic pseudo-answer from the prompt hash
            # (models the "solver can't reliably solve it" case).
            h = hashlib.sha256(prompt.encode()).digest()[0]
            correct = letters[h % 3]
        if self.failure_mode == "wrong_solver":
            return letters[(letters.index(correct) + 1) % 3]
        if self.failure_mode == "split_solver":
            return letters[(letters.index(correct) + sample_index) % 3]
        return correct


# ---------------------------------------------------------------------------
# claude-cli backend
# ---------------------------------------------------------------------------


@dataclass
class ClaudeCliBackend:
    model: str = "sonnet"
    binary: str = CLAUDE_BIN
    timeout: int = CALL_TIMEOUT_SECONDS
    name: str = "claude-cli"

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        # sample_index is folded into the prompt so repeated samples are
        # genuinely independent calls rather than cached repeats.
        payload = prompt
        if sample_index:
            payload = f"{prompt}\n\n(independent sample #{sample_index})"
        result = subprocess.run(
            [self.binary, "-p", "--model", self.model],
            input=payload,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude-cli exited {result.returncode}: {result.stderr[:400]}"
            )
        return result.stdout


# ---------------------------------------------------------------------------
# OpenAI-compatible backend
# ---------------------------------------------------------------------------


@dataclass
class OpenAICompatBackend:
    model: str = "gpt-4o-mini"
    base_url: str = ""
    api_key: str = ""
    timeout: int = CALL_TIMEOUT_SECONDS
    name: str = "openai-compatible"

    def __post_init__(self) -> None:
        self.base_url = self.base_url or os.environ.get("OPENAI_BASE_URL", "")
        self.api_key = self.api_key or os.environ.get("OPENAI_API_KEY", "")

    def complete(self, prompt: str, *, sample_index: int = 0) -> str:
        if not self.base_url:
            raise RuntimeError("OPENAI_BASE_URL is not set")
        url = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7 if sample_index else 0.0,
            }
        ).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Drafter + independent critic + multi-sample consensus
# ---------------------------------------------------------------------------


@dataclass
class LlmPath:
    """The two-stage generate->validate LLM path with consensus solve-check."""

    drafter: Backend
    critic: Backend
    solver: Backend
    k_samples: int = 3
    # Filled per call: an audit trail the validation report can embed.
    last_audit: dict[str, Any] = field(default_factory=dict)

    def draft_item(
        self, topic: str, cluster: str, concept: str, misconception_ids: list[str]
    ) -> dict[str, Any] | None:
        """One drafter call -> parsed draft dict (or None on parse failure)."""
        reply = self.drafter.complete(
            prompts.drafter_prompt(topic, cluster, concept, misconception_ids)
        )
        return parse_json_reply(reply)

    def critic_verdict(self, item: dict[str, Any]) -> tuple[bool, list[str]]:
        payload = json.dumps(
            {
                k: item.get(k)
                for k in (
                    "stem",
                    "choices",
                    "correct",
                    "rationale",
                    "distractor_rationales",
                    "misconceptions",
                )
            },
            indent=1,
        )
        reply = parse_json_reply(self.critic.complete(prompts.critic_prompt(payload)))
        if not reply:
            return False, ["critic reply unparseable"]
        verdict = str(reply.get("verdict", "")).lower() == "accept"
        reasons = [str(r) for r in reply.get("reasons", [])]
        return verdict, reasons

    def solver_consensus(self, item: dict[str, Any]) -> tuple[bool, list[str]]:
        """k independent solver samples must all pick the labelled answer."""
        picks: list[str] = []
        for s in range(self.k_samples):
            reply = parse_json_reply(
                self.solver.complete(
                    prompts.solver_prompt(item["stem"], item["choices"]),
                    sample_index=s,
                )
            )
            picks.append(str(reply.get("answer", "?")) if reply else "?")
        agree = all(p == item.get("correct") for p in picks)
        return agree, picks

    def generate_validated(
        self, topic: str, cluster: str, concept: str, misconception_ids: list[str]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Draft one item and run critic + consensus. Returns (item, audit).

        The returned item still has to clear the shared machine gates
        (schema, rationale, leakage) in gates.py before emission; acceptance
        is automatic once every gate passes - no human sign-off anywhere.
        """
        audit: dict[str, Any] = {
            "drafter": getattr(self.drafter, "name", "?"),
            "critic": getattr(self.critic, "name", "?"),
            "solver": getattr(self.solver, "name", "?"),
            "k_samples": self.k_samples,
            "same_family_warning": (
                "drafter and critic share a model family; independence is "
                "weakened [R23] - risk accepted by owner decision 2026-07-02"
                if _same_family(self.drafter, self.critic)
                else ""
            ),
        }
        draft = self.draft_item(topic, cluster, concept, misconception_ids)
        if not draft:
            audit["outcome"] = "draft unparseable"
            self.last_audit = audit
            return None, audit
        ok_critic, critic_reasons = self.critic_verdict(draft)
        audit["critic_accept"] = ok_critic
        audit["critic_reasons"] = critic_reasons
        ok_consensus, picks = self.solver_consensus(draft)
        audit["solver_picks"] = picks
        audit["consensus"] = ok_consensus
        if not (ok_critic and ok_consensus):
            audit["outcome"] = "rejected by critic/consensus"
            self.last_audit = audit
            return None, audit
        audit["outcome"] = "accepted by llm path"
        self.last_audit = audit
        return draft, audit


def _same_family(a: Backend, b: Backend) -> bool:
    return getattr(a, "name", "a") == getattr(b, "name", "b")


def make_llm_path(backend: str, k_samples: int = 3) -> LlmPath:
    """Build the drafter/critic/solver trio for a named backend."""
    if backend == "mock":
        return LlmPath(
            drafter=MockBackend(role="drafter"),
            critic=MockBackend(role="critic"),
            solver=MockBackend(role="solver"),
            k_samples=k_samples,
        )
    if backend == "claude-cli":
        # Different model ids, same family: documented weaker independence.
        return LlmPath(
            drafter=ClaudeCliBackend(model="sonnet"),
            critic=ClaudeCliBackend(model="haiku"),
            solver=ClaudeCliBackend(model="haiku"),
            k_samples=k_samples,
        )
    if backend == "openai-compatible":
        return LlmPath(
            drafter=OpenAICompatBackend(),
            critic=OpenAICompatBackend(),
            solver=OpenAICompatBackend(),
            k_samples=k_samples,
        )
    raise ValueError(f"unknown backend {backend!r}")
