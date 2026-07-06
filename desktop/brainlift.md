# BrainLift — Bridging Memory → Performance in Anki (CFA Level I)

## Owners

- **William** — primary owner / author

---

## Purpose

**Core goal:** choose and implement an evidence-based study technique into Anki that bridges the gap between memory and performance. The feature should transfer memorized facts to the application-style multiple-choice questions of the CFA Level I exam beyond what spaced repetition already does, and prove that it works using a clean ablation. Plain flashcards help memorization, not transfer. Thus, the thesis is that the *schedule and selection of cards*, not just their spacing, can be engineered to close that gap for CFA L1.

**Target exam — CFA Level I:** pass/fail; 180 standalone multiple-choice questions (A/B/C);
fact- and formula-heavy with an ethics/case component; organized as 10 topic areas →
readings → Learning Outcome Statements (LOS). Its defining demands — discriminating among
*confusable* categories (FIFO/LIFO/weighted-average, Macaulay/modified/effective duration,
forwards/futures/swaps, the Ethics Standards) and *applying* a formula or rule to a new
mini-scenario — are exactly the Memory→Performance gap this project targets.

**What baseline Anki already does:** it is already
retrieval practice (flashcards) + an optimized spacing engine (FSRS). Queues are ordered by
**card state and due date**, never by content; FSRS assigns each card a memory state
(stability, difficulty) toward a desired
retention (default 0.90); new and review cards are mixed via an "Intersperser"; every card
is scheduled **independently** (sibling burying is the only inter-card relationship);
production is supported via type-in/cloze; feedback is immediate; grading is
Again/Hard/Good/Easy.

**Three gauges — the questions this project's app answers (separately):** **Memory** (can the
student recall the fact?), **Performance** (can they answer a *new*, held-out question using it?),
and **Readiness** (a calibrated prediction of the score they'd get today).

**In scope:** engine-level, cleanly-ablatable techniques that target transfer for CFA L1 —
graph/cluster scheduling (`SPOV 1`), FSRS-driven faded worked examples for the formula-heavy
topics (Quant, Fixed Income, Derivatives, Corporate, Portfolio Management; `SPOV 2`), and
surfacing confusable cards for contrast (`SPOV 3`); and
the three-way ablation — **feature ON vs feature OFF vs unmodified Anki** — on the **same CFA
questions** and **equal total study time**, scored on a held-out bank of CFA-style MCQs
(Performance), delayed recall (Memory), and calibrated pass-probability (Readiness).

**Out of scope:** re-implementing retrieval practice or spacing (already in Anki/FSRS);
UI-only techniques that can't be cleanly ablated at the engine; other
exams and **CFA Levels II–III** (vignette/essay formats), plus multi-subject breadth — this
project is deliberately scoped to **CFA Level I**; debunked ideas (learning styles).

## DOK 4 — Spiky Points of View

### SPOV 1 — Schedule the graph, not the card *(spine)*

Conventional SRS treats each card as an independent atom on its own due date — but for CFA L1
that independence is the ceiling on transfer; the schedulable unit should be a *linked cluster*
of cards, not a lone card. This way, the app can work around topics, not individual questions, and
schedule questions that are either similar or different depending on the state of the user.
**Grounding:** Anki baseline (cards scheduled independently; sibling-burying is the only
inter-card relationship); transfer moderators (Pan & Rickard 2018).

### SPOV 2 — Fade the scaffold, and drive the fade off FSRS state

Novices need the worked solution while experts are slowed by it (this is known as expertise reversal), so guidance
must *fade* gradually: worked → faded (cloze, backward-fading) → solve (format-matched A/B/C MCQ). The
spiky part isn't "fading is good" (experts agree) — it's that the fade level should be read off
FSRS's **existing** stability/difficulty, not a fixed/authored schedule or a separate counter. This way, fading is determined dynamically on a per user basis.
**Grounding:** worked-example effect (Sweller & Cooper 1985; Barbieri 2023); expertise reversal
(Kalyuga 2003); generation effect (Bertsch 2007); transfer-appropriate processing (Morris 1977);
FSRS stability/difficulty baseline.

### SPOV 3 — Surface confusable cards; order them adjacently (don't space them apart)

Conventional SRS schedules each card **independently** and *spaces* related cards apart; Anki's one
inter-card rule — **sibling-burying** — even pushes same-note cards away from each other to avoid
interference. For CFA L1 that interference **is** the lesson: the scheduler should place confusable
cards — FIFO/LIFO/weighted-average, the Macaulay/modified/effective duration trio, and neighbouring
Ethics Standards — **back-to-back** so the learner is forced to *discriminate* them. The precise
lever is **adjacency ordering, not un-burying**: on a recall deck these confusables are *separate
notes* (not same-note siblings), so sibling-burying doesn't even apply — the scheduler instead
reorders the gathered queue so cards sharing a cluster (derived from their **tags**) sit adjacent.
This way the user doesn't just memorize isolated facts; they also see how related ideas contrast and
build on one another.
**Grounding:** interleaving is strongly material-dependent — overall g=0.42, perceptual
categories ~0.67, math meta-average ~0.34 (Brunmair & Richter 2019), but *problem-type*
discrimination (the CFA case) d=0.42→0.79 at a month (Rohrer, Dedrick & Stershic 2015; Rohrer &
Taylor 2007). We wager CFA's confusables sit toward the upper end — an open question the ablation
tests. Anki's sibling-burying (same-note only) is the baseline this departs from.

---

## Experts

The thesis rests on a panel: **Carl Hendrick** synthesizes the field, and four primary researchers
anchor the pillars below.

### Carl Hendrick — the synthesizer

Translates the science of learning into practice (*How Learning Happens* with Kirschner;
*Instructional Illusions*); professor at Academica University, UNESCO IBE *Science of Learning*
board. His recurring theme is the **research–practice gap**: well-evidenced techniques become
"lethal mutations" when misapplied — e.g., retrieval drilled before comprehension, or under load —
the exact failure mode this project must design around. **Relevant to:** the overall throughline — *retrieval/spacing **consolidate**; construction,
induction, and transfer need **desirable (not excessive) difficulty**, faded guidance, and
discrimination* — plus the consolidation-vs-construction and retrieval×load caveats that keep the
build honest. *(DOK 2 #9; ties `SPOV 1`–`3` together.)*

### Steven C. Pan — transfer of learning

Transfer-of-learning researcher behind the transfer-of-testing meta-analysis (Pan & Rickard 2018)
and the pretesting work (Pan & Carpenter 2023). His synthesis of 192 effect sizes is the field's
definitive map of *when* the testing effect generalizes versus stays inert — the empirical backbone
of this project's "does it transfer?" question. **Relevant to:** the core Memory→Performance
thesis — *whether* testing transfers and the moderators it needs (format-match, elaboration,
initial success), which DOK 3 turns into a scheduler spec. *(DOK 1/2 "transfer of testing,"
"pretesting"; → SPOV 1.)*

### John Sweller — cognitive load & worked examples

Originator of **Cognitive Load Theory** (UNSW); the worked-example effect (Sweller & Cooper 1985).
CLT — among the most influential theories in instructional design — explains learning through the
limits of working memory, and is the parent framework behind worked examples, expertise reversal,
and fading. **Relevant to:** `SPOV 2` (worked→faded→solve) and the retrieval-under-load boundary — the case
for building/showing before drilling. *(DOK 2 #6, #9; → SPOV 2.)*

### Doug Rohrer — interleaving & discrimination

Showed interleaved math practice forces choosing the right method *from the problem itself*
(Rohrer, Dedrick & Stershic 2015; Rohrer & Taylor 2007). His classroom randomized trials (USF)
moved interleaving out of the lab into real instruction and reframed it as a trainer of *strategy
selection* — recognizing which method a problem calls for, the exact demand of a cumulative exam.
**Relevant to:** `SPOV 3` (surface
confusables) and the formula/problem-type contrast — the closest analog to CFA quant.
*(DOK 2 #3; → SPOV 3.)*

### John Dunlosky & Katherine Rawson — utility & successive relearning

The utility-ratings review (only testing + spacing rated "high"; Dunlosky 2013) and **successive
relearning** (retrieval-to-criterion; Rawson & Dunlosky 2013/2018). Their Kent State lab produced
both the field's most-cited "what actually works" audit and the successive-relearning paradigm —
together the closest thing to an evidence-ranked playbook for durable learning. **Relevant to:** the baseline
premise (retrieval + spacing already "won") and the **mastery criterion** inside `SPOV 2`'s gate.
*(DOK 2 #2, #4.)*

---

## DOK 3 — Insights

- **The transfer moderators are a scheduler spec, not study advice.** Pan & Rickard's three
conditions — format-match, elaboration, initial success — are usually read as tips, but each
maps to an engine knob: format = rung type (cloze vs MCQ), elaboration = card transform,
success = promotion gate. *Common practice wrong:* treating transfer as a property of content.
*New rule:* build the scheduler **to the moderators**. *(→ SPOV 1.)*
- **Anki's one inter-card rule trades Performance for Memory.** Sibling-burying enforces *spacing*
(which strengthens the **Memory** gauge) but suppresses *contrast* (which the **Performance**
gauge needs for discrimination) — so the only relationship Anki models buys retention at
transfer's expense, a tension no single source states. *Common practice wrong:* treating burying
as free. *New rule:* for confusable clusters, schedule for contrast even at a spacing cost.
*(→ SPOV 3.)*
- **Fade on an expanding schedule, not per session.** Cepeda's optimal gap grows with the
retention interval; applied to *scaffolding* (not just review), peel one step per expanding
interval. *New rule:* fading is a spacing decision, not a per-sitting one. *(→ SPOV 2.)*
- **Distractors and interference edges are the same artifact.** The misconception-based wrong
answers the item-generators emit (forgot ÷k, used annual) *are* the confusability coordinates
the interleaver needs — authoring one authors the other. *New rule:* build item-generation and
discrimination-scheduling together. *(→ SPOV 2 + SPOV 3.)*

---

## DOK 2 — Knowledge Tree

**1. Retrieval, generation & transfer**

- **Retrieval practice (testing effect)** — Rowland (2014); Adesope et al. (2017): practice
testing beats restudy and every other control, recall > recognition — retrieval *changes*
memory, it doesn't just measure it. Anki's flashcards already *are* this.
- **Transfer of testing** — Pan & Rickard (2018): testing transfers to new questions at
d≈0.40, but mainly across formats and to application/inference items, and mostly when
practice is format-matched, elaborated, and initially successful — strip those and transfer
collapses toward zero.
- **Generation effect** — Bertsch et al. (2007): producing an answer beats reading it
(d≈0.40, larger after a day) — type-in/cloze out-teach passive review.
- **Transfer-appropriate processing** — Morris, Bransford & Franks (1977): memory is best
when study processing matches the test, so practicing *application under retrieval* is what
bridges memory→performance (i.e., drilling CFA-style application MCQs, not just recall).

**2. Spacing — already owned by FSRS**

- **Distributed practice** — Cepeda et al. (2006): spaced beats massed; the optimal gap grows
with the retention interval (as a decreasing ratio) — exactly FSRS's per-card job.
- **Utility ranking** — Dunlosky et al. (2013): of 10 techniques, only practice testing and
distributed practice earned "high utility" — and Anki ships both already.

**3. Interleaving — a discrimination trainer**

- **Interleaving (perceptual/visual categories)** — Brunmair & Richter (2019): moderate on
average (g=0.42) but strongly material-dependent — large for confusable/visual categories
(paintings g=0.67), modest for math (0.34), *negative* for word lists (−0.39).
(Tension: Dunlosky rates it only "moderate"; Donoghue & Hattie d≈0.47.)
- **Interleaving in math (problem-type discrimination)** — Rohrer, Dedrick & Stershic (2015);
Rohrer & Taylor (2007): mixing *problem types* (vs blocking) forces choosing the right method
*from the problem itself* — classroom RCT d=0.42 immediate → 0.79 at a month. This is the
closer analog for CFA's *formula/problem-type* confusables than the perceptual-category
meta-analysis, and CFA L1's distractor-heavy MCQs are precisely a "which approach applies" test.

**4. Successive relearning & mastery**

- **Successive relearning** — Rawson & Dunlosky (2013, 2018): retrieval to a mastery
criterion across spaced sessions; lab d=1.5–4.2, ~a letter-grade in class growing to ~40%
a month later. It is retrieval + spacing + a criterion — well suited to CFA L1's large,
long-horizon fact/ethics-rule volume.

**5. Pretesting — surveyed, but a memory booster, not a transfer engine** *(boundary evidence)*

- **Pretesting** — Pan & Carpenter (2023); 2023 meta-analysis: a wrong guess before study
helps the *tested* item (g≈0.54) but barely transfers to *untested* material (g≈0.04). Because
CFA is graded on *novel* application items, this is the wrong lever — kept here only to show it
was considered and consciously set aside (it earns no SPOV).

**6. Worked examples, fading & expertise reversal**

- **Worked-example effect** — Sweller & Cooper (1985); Barbieri et al. (2023): studying
solutions beats unguided solving for novices — relevant to CFA's formula-heavy topics
(TVM, bond pricing, ratios, option payoffs).
- **Expertise reversal + fading** — Kalyuga et al. (2003); Renkl & Atkinson (2003): that
advantage *reverses* as expertise grows, so guidance must be faded toward independent
solving.

**7. Moderate-but-costly elaboration**

- **Self-explanation** — Bisra et al. (2018): "why/how" prompts give g≈0.55, contingent on
explanation quality and prior knowledge, at a time cost.
- **Elaborative interrogation** — Donoghue & Hattie (2021): "why is this true?" ≈ d 0.56,
best with prior knowledge and precise, self-generated answers.

**8. What does NOT move the needle**

- **Feedback timing** — Kandemir et al. (recent); Kulik & Kulik (1988): immediate vs delayed
≈ null on average (g≈0.03) — not a lever; Anki's immediate feedback is already fine.
- **Learning styles** — Pashler et al. (2008); 2024 meta-analysis: no credible support for
matching instruction to "style"; classified as a neuromyth.

**9. Boundary conditions — when retrieval & transfer *don't* deliver (the Expert's caveats)**

- **Consolidation vs construction** — Koedinger, Corbett & Perfetti (2012): the KLI framework
splits learning into *memory/fluency*, *induction/refinement*, and *understanding/sense-making*
— so retrieval (a memory process) strengthens traces but doesn't by itself do induction.
"Remembering is not knowing"; understanding must be built before drilling consolidates it.
- **Retrieval under high cognitive load** — Redifer et al. (2025): with demanding material,
retrieval practice gave no edge over rereading and *cognitive load fully mediated* the effect
(higher load → worse delayed test) — so sequence retrieval *after* comprehension, not during it.
- **Near vs far transfer** — Barnett & Ceci (2002): transfer is multi-dimensional (content ×
context), so a single "far-transfer" effect size is misguided. Near transfer (same domain, new
surface) is achievable — exactly the CFA target — while far transfer is rare.

---

## DOK 1 — Facts

- **Retrieval/testing** — practice testing beat restudy and all other comparison conditions
([Adesope et al. 2017](https://doi.org/10.3102/0034654316689306)); recall tests > recognition
([Rowland 2014](https://doi.org/10.1037/a0037559)). The "testing effect" is among the most
replicated results in learning science: the *act of retrieving* strengthens a memory more than
re-reading it does. Production-format tests (recall) build more durable memory than recognition
(multiple-choice), and the gap typically *widens at longer delays* — so testing is a learning
tool, not just a measurement.
- **Transfer of testing** — d=0.40 across 192 effect sizes / 122 experiments (N=10,382);
near-zero once format-match, elaborated retrieval, and initial success are removed
([Pan & Rickard 2018](https://doi.org/10.1037/bul0000151)). Testing *does* carry over to new
questions, but mainly across formats and to application/inference items — and only when the
practice was format-matched, elaborated, and initially successful. Strip those moderators and the
benefit collapses toward zero, so transfer is real but **conditional**, not automatic.
- **Utility ratings** — practice testing + distributed practice = *high*; elaborative
interrogation, self-explanation, interleaving = *moderate*; rereading, highlighting,
summarization, keyword mnemonic, imagery = *low*
([Dunlosky et al. 2013](https://doi.org/10.1177/1529100612453266)). The review graded 10 common
techniques across diverse learners, materials, and test types, so "high utility" means *robust
and general*, not merely large in one study. Tellingly, the techniques students use most
(rereading, highlighting) ranked lowest — popularity ≠ effectiveness.
- **Spacing** — spaced > massed; optimal inter-study gap grows with the retention interval
([Cepeda et al. 2006](https://pubmed.ncbi.nlm.nih.gov/16719566/)). Distributing study across
sessions beats cramming the *same total time*, and the best gap scales with how long you must
retain — a longer retention horizon calls for a longer gap (as a decreasing ratio). This
expanding-gap principle is exactly what FSRS automates per card.
- **Interleaving** — overall g=0.42; paintings 0.67; math 0.34; expository text n.s.; words
−0.39; 59 studies / 238 effect sizes
([Brunmair & Richter 2019](https://pubmed.ncbi.nlm.nih.gov/31556629/)). Mixing different
categories or problem types (instead of blocking one at a time) helps learners *discriminate
which approach applies*. But the effect is strongly material-dependent — large for confusable
visual categories, modest for math, and even *negative* for arbitrary word lists — so it is a
discrimination tool, not a universal good.
- **Interleaving in math (problem-type)** — interleaved > blocked for choosing *which method
applies*; classroom RCT d=0.42 immediate, 0.79 at 30-day delay
([Rohrer, Dedrick & Stershic 2015](https://doi.org/10.1037/edu0000001);
[Rohrer & Taylor 2007](https://doi.org/10.1007/s11251-007-9015-8)). Blocked practice tells
students the method *before* they read the problem, so it never trains strategy selection;
interleaving forces choosing the approach from the problem itself — the skill a cumulative exam
demands. Notably the advantage *grew* from the immediate to the delayed test, the opposite of a
cramming illusion.
- **Successive relearning** — ~letter-grade classroom gain → ~40% higher a month later
([Rawson, Dunlosky & Sciartelli 2013](https://doi.org/10.1007/s10648-013-9240-4)); lab
relearning potency d=1.52–4.19 ([Rawson et al. 2018](https://doi.org/10.1037/xap0000146)). The
method is retrieval *to a mastery criterion* (e.g., recall correctly N times) repeated across
spaced sessions — i.e., retrieval + spacing + a criterion bolted together. Its effects are among
the largest in the literature, and crucially the advantage *widens* over the retention interval.
- **Pretesting** — specific (tested) effect g=0.54 (k=97); general (untested) effect g=0.04
(k≈91) ([2023 meta-analysis](https://pubmed.ncbi.nlm.nih.gov/37640836/);
[Pan & Carpenter 2023](https://doi.org/10.1007/s10648-023-09814-5)). Guessing *before* being
taught (errorful generation) reliably boosts memory for the *specific items* you were pretested
on, yet that gain barely spreads to related, untested material. So pretesting is a targeted
memory booster, not a transfer engine — which is why it earns no SPOV here.
- **Generation effect** — d=0.40 (cued recall 0.55, free recall 0.32; >1-day delay 0.64);
86 studies / 445 effect sizes ([Bertsch et al. 2007](https://doi.org/10.3758/bf03193441)).
*Producing* an answer (filling a blank, completing a cloze) beats reading the same answer,
because generation forces effortful processing instead of passive recognition. The benefit is
larger after a delay, which is why type-in/cloze cards out-teach plain front-and-back review.
- **Self-explanation** — g=0.55 (95% CI .45–.65); 69 effect sizes / 5,917 participants
([Bisra et al. 2018](https://eric.ed.gov/?id=EJ1186664)). Prompting learners to explain *how* and
*why* as they study reliably improves understanding, but the size depends on the *quality* of the
explanations and on prior knowledge — and it adds time. It's a solid, general booster with a real
cost attached, which is why it sits in the "moderate-but-costly" tier rather than as a lever.
- **Cross-technique d's** — distributed 0.85, practice testing 0.74, elaborative
interrogation 0.56, self-explanation 0.54, interleaving 0.47
([Donoghue & Hattie 2021](https://doi.org/10.3389/feduc.2021.581216)). Placing the major
techniques on one common scale lets you rank them directly, and the ordering broadly corroborates
Dunlosky — distributed practice and testing sit on top. It's a useful cross-check that the two
techniques Anki already ships are the highest-leverage ones.
- **Worked examples** — worked > unguided for novices (Sweller & Cooper 1985;
[Barbieri et al. 2023](https://www.danamillercotto.com/uploads/4/7/7/2/47725475/barbieri_et_al__2023__we_meta-analysis.pdf));
**expertise reversal** — benefit reverses with expertise
([Kalyuga et al. 2003](https://doi.org/10.1007/s11251-009-9102-0)). For a novice, studying a full
solution beats unguided problem-solving because it avoids overloading working memory with
means-ends search. But as competence grows the guidance becomes redundant and *slows* the
learner, so support must be **faded** toward independent solving — the basis for worked→faded→solve.
- **Feedback timing** — immediate vs delayed ≈ null, g=0.03 (95% CI [−0.08, 0.13]) (Kandemir
et al., recent; Kulik & Kulik 1988). Whether feedback arrives instantly or after a delay makes
essentially no average difference, and the confidence interval straddles zero. Timing is
therefore not a lever worth engineering — Anki's immediate Again/Hard/Good/Easy feedback is
already fine.
- **Learning styles** — no support for the meshing hypothesis; a neuromyth
([Pashler et al. 2008](https://doi.org/10.1111/j.1539-6053.2009.01038.x);
[2024 meta-analysis](https://doi.org/10.3389/fpsyg.2024.1428732)). The "meshing" claim — that
matching instruction to a learner's preferred style (visual/auditory/etc.) improves learning —
repeatedly fails the proper experimental test (the style × instruction interaction). Despite
enormous popularity it is classified as a neuromyth, and is explicitly out of scope here.
- **Transfer-appropriate processing** — memory best when study processing matches the test
(Morris, Bransford & Franks 1977). Performance depends not just on *how well* you encoded but on
whether the *kind* of processing at study matches what the test requires. For an application
exam, that implies practicing **application under retrieval** (CFA-style MCQs), not recall alone —
the principle that assigns the MCQ format to the solve rung.
- **Retrieval × cognitive load** — with demanding material, retrieval practice ≈ rereading on the
final test; cognitive load *fully mediated* performance (load→score B=−0.54)
([Redifer et al. 2025](https://doi.org/10.1007/s11251-025-09758-z)). When working memory is
already saturated by complex content, the extra load of effortful retrieval cancels its usual
benefit. The practical rule is to *sequence retrieval after comprehension* — once the material is
understood enough to leave spare capacity for retrieval to work.
- **Knowledge-Learning-Instruction (KLI)** — three learning-event classes: memory/fluency,
induction/refinement, understanding/sense-making — memory ≠ induction
([Koedinger, Corbett & Perfetti 2012](https://doi.org/10.1111/j.1551-6709.2012.01245.x)). The
framework holds these are *different* processes with *different* optimal instruction, so a memory
process (retrieval) cannot by itself produce induction or understanding. It's the formal basis
for "remembering is not knowing" and for treating construction as a stage in its own right.
- **Near vs far transfer** — transfer is multi-dimensional (content × context); a single
far-transfer effect size is misguided; near transfer achievable, far transfer rare
([Barnett & Ceci 2002](https://doi.org/10.1037/0033-2909.128.4.612)). The taxonomy spans nine
content/context dimensions, so "did transfer occur?" only means something relative to *how far*.
Near transfer — same domain, new surface (a fresh CFA item of a learned LOS) — is well supported,
whereas far transfer across domains is rare, which is why this project targets the near case.

---

## Design Process

**Thesis-first, then evidence.** The three Spiky Points of View came first; the build only started once
each was pinned to graded evidence. A structured deep-research fan-out — nine evidence threads (T1–T9),
each source-graded, then **adversarially verified** with a devil's-advocate refute pass and citation
checks, then synthesized — turned the thesis into a cited research report (`RESEARCH_ADDENDUM.md`) and a
v1→v2 plan revision (`PLAN_CHANGELOG.md`) carrying **29 concrete changes**. The headline finding reshaped
the project: each SPOV **survived but became *bounded* by a measured moderator** — transfer is
moderator-carried and credited **within-topic only**; the fade gate should key on **spaced-session
count**, not a within-session criterion; and confusable adjacency must sit behind a ***signed* similarity
gate**, because the ordering rule flips sign for dissimilar items (juxtaposing merely-similar pairs is a
measured loss).

**Adversarial grilling before code.** Two independent "grillers" (one on grade/scope, one on
engine-correctness) stress-tested the v2 plans into a superseding errata file (`GRILLING_NOTES.md`,
corrections C1–C14). This caught a **live auto-fail before it shipped** — the Readiness gauge was emitting
a *point* pass-probability behind a give-up rule far weaker than promised — and several planned items that
were simply **wrong or infeasible in the real engine** (a mathematically incorrect fade formula, re-gating
on every answer, an inert config field). Replacing that gauge with an honest band + real abstention gate
became the highest-leverage change.

**Phased, ablation-driven build.** Work shipped in three phases, each behind a **default-off toggle** with
unit tests, so every feature is a clean ablation arm:

- **Phase 1** — contrast scheduling (`SPOV 1` + `SPOV 3`) + the per-topic mastery RPC. No AI, no content
generation.
- **Phase 2** — the FSRS fade ladder (`SPOV 2`) + the authoring-time AIG content pipeline.
- **Phase 3** — the banded, calibrated, abstaining Readiness gauge, cross-layer unification, and the
rigorous three-arm ablation (**full / feature-off / vanilla Anki**).

**Correctness came from reading the real engine, not the plan.** Two architecture errata (`[A1]`/`[A2]`)
surfaced only by reading Anki's actual queue builder: rung-gating had to live in the **gather path as a
bury-style skip** (the post-gather contrast seam is pure reordering and can't preserve deck limits), and
the fade signal had to be computed **before the gather query** (FSRS memory state isn't in the lightweight
queue structs). The build follows an outcome-oriented spec ethos — fix the *what*, the *invariants*, and
the *acceptance*; choose the simplest thing that works; build behind the wall; and **self-verify each
milestone with a fresh-context verifier subagent** rather than self-critique.

**Verification is heavy and mostly automated.** The fork carries **~651 Speedrun-authored automated tests**
(570 Python, ~53 Rust, 28 TypeScript) plus **17 runnable measurement harnesses** that each write a
committed eval report — performance vs targets, SIGKILL crash-safety (0 of 20 collections corrupted),
offline sync, prompt-injection resistance, held-out-probe validity, FSRS calibration, and the ablation —
all gated by a green `./check`. On top of that sits a **five-persona AI user-test program** (novice /
veteran / skeptic / UX / auditor) that drove the **real headed Qt build over Chrome DevTools Protocol** —
real DOM clicks through the reviewer and dashboard, dropping to the engine for adversarial truth-checks —
producing 123 evidence artifacts. The honesty contract held both **adversarially** (the skeptic could not
make the honest-input path lie) and **mechanically** (26/26 contract checks, zero tracebacks). Every
material owner decision is logged with a date in `PLAN_CHANGELOG.md` (e.g., making the AIG pipeline fully
human-free; promoting the fade engine to committed scope).

---

## Architecture

**A fork of Anki, respecting its layers.** The project is built *into* Anki's multi-layer architecture
rather than beside it, so one commit builds desktop and AnkiDroid off the same `26.05b1` engine: a **Rust
core** (`rslib`) for scheduling/stats, a **Python library** wrapping Rust via `rsbridge` (`pylib`), a
**PyQt GUI** (`aqt`) that embeds **Svelte/TypeScript** web components (`ts`), and **protobuf** definitions
(`proto`) as the typed IPC between layers. New capabilities were added as engine modules, RPCs, and web
routes — with **no new synced database table**.

**The three-gauge spine.** Every feature maps to one of the three questions the app answers separately —
**Memory** (can you recall the fact?), **Performance** (can you answer a *new*, held-out question?), and
**Readiness** (a calibrated pass-probability today).

**Engine (`rslib`).**

- **Scheduler / queue builder** — `scheduler/queue/builder/contrast.rs` realizes `SPOV 1` + `SPOV 3` as a
**pure reordering of the merged queue**: confusable-cluster siblings placed back-to-back, a
sibling-adjacency guard, within-topic-only cluster keys, and a signed `confusable::high` gate so
merely-similar clusters keep default spacing. `builder/fade.rs` realizes `SPOV 2` as **build-time rung
gating** — worked→faded→solve, exactly one rung per cluster admitted via a bury-style skip; fade signal =
predicted retrievability at the exam horizon; two-sided hysteresis; a spaced-session promotion gate read
from the revlog.
- **Readiness** (`readiness/`) — a **banded, two-number, abstaining** estimate: a Beta(x+½, n−x+½) Jeffreys
posterior over **delayed held-out probe** outcomes, mapped through a Binomial(180, p) exam-score model
against a configurable minimum-passing-standard band (`speedrun:passBand`, default [0.68, 0.75]). The
**give-up rule is enforced in the backend** (≥300 graded reviews, ≥70% weighted coverage, ≥50 held-out
probes, half-width ≤ 0.20), and an abstaining response **zeroes every number in its constructor**, so no
display layer can leak an unearned pass %.
- **Stats** — a per-topic mastery RPC (`stats/mastery.rs`) powering the dashboard Memory gauge, and a
concept-graph RPC (`stats/concept_graph.rs`) for the knowledge map.

**Tags as the edge model; config as the knobs.** Clusters, ladder rungs, topics, interactivity and
confusability are all **encoded in note tags** (`cfa::topic::`, `cluster::`, `rung::`, `interactivity::`,
`confusable::high`, `aig::ungraded`) instead of a new relational schema, and all user state lives in
**synced collection config** (`speedrun:tagTopicMap`, `speedrun:exam_date`, `speedrun:passBand`, and the
default-off `speedrun:`* AI toggles). On-demand **topic speedruns** (`qt/aqt/speedrun_study.py`) reuse
Anki's own filtered-deck engine — a per-topic `tag:cfa::topic::<id>` search, rescheduled in place — with
**no scheduler changes**.

**Web surfaces.** Two SvelteKit routes render the gauges: a **dashboard** (`ts/routes/dashboard`, whose
`metrics.ts` builds the `DashboardModel` — per-subject Memory/Performance, coverage, weighted gap,
best-next — plus the abstention message and the optional AI panels) and a **concept graph** (a
force-directed knowledge map with honest no-data colouring). Both are served through an API-enabled
`AnkiWebView` on desktop and from the `.aar` on Android, which **degrades gracefully** (AI affordances hide
when the desktop host bridge is absent).

**The wall.** The load-bearing architectural invariant is that **the review loop is AI-free by
construction**: no AI call ever touches grading, scheduling/gating, or the Readiness input. AI is confined
to two places that only ever *read and narrate* — an **authoring-time** pipeline (`tools/speedrun/aig`,
which bakes items into JSONL consumed by the deck builder) and a **runtime assistant layer**
(`tools/speedrun/assistant`, routed through a desktop host bridge that deliberately bypasses the Rust
backend). And the **instrument can't feed itself**: held-out probe pools (`probe::pool::`*) are excluded
from Memory/coverage, probe answers don't count as study reviews, and ungraded generated cards are
quarantined from Readiness.

---

## AI Features

All AI in the product lives **outside the review loop** and behind one honesty pattern —
**grounded-or-abstain** — so it can inform the learner without ever fabricating the numbers the app exists
to keep honest.

### 1. Authoring-time item generation (AIG)

An offline generate→validate pipeline (`tools/speedrun/aig`) produces net-new CFA-style A/B/C items:

- **Parameterized numeric generators** are the guaranteed-runnable shipped content, with
**misconception-grounded distractors** — each wrong choice embodies a named student error, which is the
same artifact the interleaver needs (see DOK 3).
- An optional **LLM drafter → critic → solver** path runs over pluggable backends (`mock` / `claude-cli` /
`openai-compatible`), with a deliberate **independence requirement** (drafter ≠ critic model) and
**k-sample self-consistency** consensus on the answer.
- **Machine validation gates run on *every* item**: dual-implementation numeric agreement, a
single-best-answer solve-check, feedback/rationale completeness, schema conformance, and a **leakage
wall** that n-gram-checks each stem against a local CFA reference PDF (extracted at runtime only, never
stored or committed) and rejects any ≥ 8-token overlap. Acceptance is **fully automatic — no human
sign-off** (an owner decision that explicitly accepts automation-bias risk); the compensating control is
that **ungraded generated items never feed Readiness** — they carry `aig::ungraded` and are auto-retired
by live point-biserial discrimination.
- **Retrieval-for-grounding** attaches a named source passage to each item and is evaluated on held-out
synthetic qrels; the fusion arm (**BM25 + dense → RRF → cross-encoder rerank**) measured P@1 0.727 vs
tuned BM25 0.500 / dense 0.455, with latency reported and the dense arm honestly marked *unavailable*
when its ML stack isn't present rather than faking a number.
- A **behavioral confusability signal** mines the revlog for confusion (`surface_similarity × discrimination_need`, within-topic), **auto-validates on a 70/30 time split** (it must beat a
surface-only baseline or it **abstains**), and writes the `confusable::high` markers that gate `SPOV 3`
adjacency.

### 2. Runtime assistant (default-off, read-only)

Three features sit on the dashboard / session surfaces (`tools/speedrun/assistant`), each gated by a synced
toggle and each falling back to a deterministic view when off, offline, or erroring:

- **(A) Post-session debrief** — turns a session's misses into a short pattern narrative (topics/clusters
missed, **confusable pairs that co-occurred**, the misconceptions behind wrong MCQs, one next step). It
is the most on-thesis feature: it narrates the `SPOV 3` confusion signal the repo already computes.
- **(B) Study Coach** — a grounded "what should I do today?" that prioritizes by weighted gap and best-next
from the dashboard model, and **defers to the gauge**: while Readiness abstains it **must not invent a
pass-probability** and instead echoes the abstention reasons verbatim.
- **(C) Tag→topic suggester** — pre-fills the *Map tags* editor for unmapped deck tags, but **AI never
persists**: the human reviews and Saves before `speedrun:tagTopicMap` is written, and low-confidence
suggestions are left blank.

### 3. The grounded-or-abstain core & safety

Every runtime call goes through `grounded_complete`, which hands the model **only facts the app already**  
**computed**, demands a JSON reply, and **abstains (returns `None`)** whenever the reply is unparseable,  
self-reports low confidence, fails its schema, or **states any number not present in the supplied facts**  
(a numeric-grounding check walks the reply and matches every value, rounding, and percent form against the  
facts). A hard timeout and a catch-all make any error abstain, too, and the caller renders its deterministic  
view. The wall is reinforced by disclosure of AI output and any network egress, mandatory human-in-the-loop  
before attribution is persisted, and a **prompt-injection resistance eval** (6 payloads × 4 model-facing  
surfaces, all passing). The five-persona user-test independently confirmed the assistant was  
**default-OFF, server-side re-checked, read-only, and refused to state scores while abstaining**.

