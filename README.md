# agentic-principle

> Decide whether the job needs an agent at all — then how to split it, orchestrate it, and sign it off.

**English** · [中文文档](./README.zh-CN.md)

An agent skill that turns a business intent into an agreed-upon agentic design: scenario selection, agent and tool boundaries, system prompts, context and concurrency budgets, and the information chain between every step. It also works in reverse — as a review lens on systems you have already shipped.

## Why this exists

Most agentic projects go wrong long before anyone writes a prompt.

- **Nobody asks whether the task needs an LLM at all.** A deterministic 40-line script gets rebuilt as three collaborating agents — slower, costlier, and less reliable than the thing it replaced.
- **Scope is never pinned down.** The agent ships, and only then does it turn out it never had access to the rule it needs to make the judgment it was built to make. It improvises instead, convincingly.
- **Sub-agent fan-out has no ceiling.** The first real workload blows past the LLM concurrency quota, and the failure looks like a bug rather than a capacity decision nobody made.
- **The system prompt is a fossil.** It was written once, the business logic moved on, and no one can say when it was last confirmed to still be correct.
- **There is no design to point at.** Nobody can name the moment the approach was agreed, so every disagreement reopens the whole thing.

None of these are prompt-engineering problems. They are architecture and scoping problems, and they are all decidable up front — which is exactly what this skill forces you to do.

## What you get

**A classification you cannot skip.** Five scenarios, judged top-down, first match wins. Plus a boundary table for the four pairs that actually get confused in practice — because "this is a workflow" and "this needs sub-agents" look identical until you ask the right question.

**A complexity ladder.** The classification gives you the ceiling; the ladder makes you take the lowest viable rung inside it. Every rung up costs latency, money, and determinism.

**A six-element contract for every system prompt.** Role → responsibilities → process → quality bar → output format → edge cases. None of them optional. Output format is the one teams drop, and it is the one that breaks the downstream parser.

**A mandatory completeness scan.** Before you show anyone a design, you walk every agent node and verify it can actually reach the rules it needs for every judgment it makes. Gaps go to the user as a list. Inventing a business rule to fill a gap is a hard prohibition, not a preference.

**A mandatory attention-load check.** The other half of the pair: walk every agent against hard limits — how long the prompt runs, how much optional material is stuffed into turn one, how many scenarios one agent has to juggle, whether key intermediate conclusions live only in conversation history. Cross a limit and you must act: push down, split, route, or checkpoint. Relief comes from moving information to another layer or splitting responsibilities — **never from deleting a rule the agent needs to judge with**.

**A two-track sign-off.** Structured questions in batches of four, then a design doc with a flow diagram for final review. The design is not "agreed" until someone says so, and the skill records where and when.

**A review mode.** Point it at an existing implementation and it derives an independent design *first*, then diffs — so you catch the scenario-selection mistakes that a straight code read would anchor you past. The diff includes an attention-load assessment: issues come with evidence, **you confirm they are real issues first**, and only then does it propose improvements. It never edits your implementation on its own.

## The classification

| Question (top-down, first match wins) | Scenario | Example |
|---|---|---|
| Fully deterministic flow, no step needs semantic judgment? | **Plain program** | Batch file rename; fixed-rule monthly report |
| Only produces text or advice, never touches external state? | **Single-agent chat** | Internal policy Q&A; copy editing |
| Step order and branches enumerable at design time, orchestrated by code? | **Agentic workflow** | Contract review pipeline: parse → parallel review → summarize |
| Decomposition only knowable at runtime, or needs parallelism / context isolation? | **Parent-child agentic** | Full security audit of an unfamiliar codebase |
| None of the above — agent-centric, user talks to it, it calls tools autonomously | **Single agentic** | Pair-programming assistant |

Hit "plain program" and the skill tells you to stop using it. That is a feature.

Adjacent scenarios are where the mistakes live, so there is a second table for exactly those four boundaries — e.g. *workflow vs parent-child* comes down to one question: is the subtask list known at design time, or only after the agent goes and looks?

For workflows, orchestration mode is a second-level choice drawn from [Anthropic's Building Effective Agents](https://www.anthropic.com/research/building-effective-agents): prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer.

## The workflow

| Step | What happens |
|---|---|
| 0 | Intent and boundaries — vision, users, success criteria, explicit non-goals |
| 1 | Scenario classification, then take the lowest viable rung of the complexity ladder |
| 2 | Draft the design in-session. Nothing written to disk yet |
| 2.5 | **Information completeness scan** — every agent node, no sampling. Produces a gap list |
| 2.6 | **Attention-load check** — every agent against hard limits; crossings get pushed down, split, routed, or checkpointed |
| 3 | Two-track sign-off: batched questions, then design doc for final review |
| 4 | Implement or diff against the existing system. Live-test every agent before calling it done |

## What's in the box

The skill itself is the `agentic-principle/` subdirectory — that is the unit of distribution. The READMEs at the repo root are not part of it.

```
agentic-principle/
├── SKILL.md
└── references/
    ├── agent-construction.md
    ├── system-prompt-blocks.md
    └── design-doc-template.md
```

| File | Contents |
|---|---|
| `SKILL.md` | Entry point. Classification, workflow, core principles, per-scenario checklists, hard prohibitions |
| `references/agent-construction.md` | Six-element prompt template, writing rules, minimum capability sets, live-test procedure |
| `references/system-prompt-blocks.md` | 12 reusable system-prompt blocks with an applicability matrix. One is mandatory |
| `references/design-doc-template.md` | Authoritative question batches, design doc skeleton, flow diagram conventions |

Tool names throughout are written as **capability classes** (file read, content search, path match, write, command execution, network, sub-agent dispatch), not as any one harness's tool names. Map them to whatever your environment actually calls them.

## Install

```bash
git clone git@github.com:weidwonder/agentic_principle.git
cp -r agentic_principle/agentic-principle ~/.claude/skills/
```

Or keep the clone where you develop and symlink the subdirectory, so `git pull` updates the installed skill:

```bash
ln -s "$PWD/agentic_principle/agentic-principle" ~/.claude/skills/agentic-principle
```

The skill directory is self-contained — no CLI, no dependencies, no login.

## Usage

Say what you want in plain language:

- "Design the agent architecture for our contract review feature"
- "Should this be a workflow or should the agent dispatch sub-agents?"
- "Review the agent system we shipped last quarter"

The skill will classify the scenario, tell you its reasoning, and start asking. Expect it to push back if your idea is over-engineered — and expect it to say so plainly if the answer is "this should just be a script."

## Non-goals

This skill does not write your business code, and it does not generate plugin-format agent config files — that is what `agent-creator` / `agent-development` are for. It designs the architecture; something else builds it.

It also will not let you skip the sign-off silently. You can explicitly tell it to skip, and it will — but it will write down every assumption it proceeded on and tell you what they were.
