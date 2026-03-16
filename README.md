# distillrepo

`distillrepo` distills Python repositories into compact review bundles for LLMs and structured IR for agents.

Default outputs:
- `distilled.<package>.<MMMDDYYYY>.py`: a single-file bundle for LLM review
- `<package_root>/.distillrepo/`: a structured Intermediate Representation (IR) for agents and downstream tooling

## Why use distillrepo

Large repos are awkward to review with an LLM if you only have two bad options:
- paste raw source and waste context
- paste a vague summary and lose important detail

`distillrepo` sits in the middle:
- it preserves real code for the most relevant parts
- compresses lower-priority areas into summaries or signatures
- keeps a structured IR for retrieval, ranking, and follow-up analysis

## Example Demo Outputs

These are real runs on open-source repositories. They show the kind of compression `distillrepo` can achieve, but they should be interpreted together with root coverage and review quality, not as standalone scoreboards.

| Repo | Shape | Review mode | Files | Symbols | Distilled size | Saved | Compression |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `openai-agents-python` | Agent SDK | `review` | 163 | 1701 | 100,082 | 79.2% | 4.8x |
| `networkx` | Large API library | `review` | 288 | 1973 | 62,074 | 93.8% | 16.3x |
| `networkx` | Large API library | `budgeted` | 288 | 1973 | 41,872 | 95.8% | 24.1x |
| `rich` | Medium utility library | `review` | 100 | 833 | 15,964 | 94.6% | 18.5x |

Please note that `distillrepo` uses heuristics for root inference, reachability, hotspot ranking, and unused-code detection. Those are useful review aids, but they are not ground truth.

### `openai-agents-python`

Good demo for an agent-native audience: handoffs, tools, tracing, memory, model adapters, and runtime orchestration all live in one package.

- `163` files, `1701` symbols, `163` modules
- `482,255` estimated original tokens -> `100,082` distilled tokens
- `79.2%` saved, `4.8x` compression
- `96` modules reached from the inferred root set

Why it is useful: the LLM bundle keeps the core agent runtime and API surface reviewable in one file, while `.distillrepo/` gives agents a reusable symbol and relationship map for follow-up inspection.

Output tree in `.distillrepo`:
```bash
>>  tree
.
├── chunks.json
├── entrypoints.json
├── hotspots.json
├── manifest.json
├── modules.json
├── relationships.json
├── repo_summary.md
├── symbols.json
└── unused_candidates.json

1 directory, 9 files
```

### `networkx`

Good demo for large API-heavy libraries: many modules, broad public surface, and enough internal structure that selective compression matters.

`review` mode:
- `288` files, `1973` symbols, `288` modules
- `1,008,946` estimated original tokens -> `62,074` distilled tokens
- `93.8%` saved, `16.3x` compression
- `222` modules reached from the inferred root set

`budgeted` mode:
- `1,008,946` estimated original tokens -> `41,872` distilled tokens
- `95.8%` saved, `24.1x` compression

Why it is useful: `review` preserves more structural and code detail for general inspection; `budgeted` shows how much further the bundle can shrink when you mainly want a compact triage artifact.

### `rich`

Good demo for a medium-sized, recognizable library with many modules and a clear internal architecture.

- `100` files, `833` symbols, `100` modules
- `295,361` estimated original tokens -> `15,964` distilled tokens
- `94.6%` saved, `18.5x` compression
- `66` modules reached from the inferred root set

Why it is useful: the repo is large enough to make manual copy-paste review awkward, but still small enough that the bundle and IR outputs are intuitive to inspect.

## Installation

```bash
pip install distillrepo
```

For richer static analysis, install the optional analyzers too:

```bash
pip install "distillrepo[analysis]"
```

## Quick Start

Analyze a package directory:

```bash
distillrepo path/to/package
```

This writes:
- `path/to/package/distilled.<package>.<MMMDDYYYY>.py`
- `path/to/package/.distillrepo/`

Show help:

```bash
distillrepo --help
```

Print the generated bundle to stdout as well:

```bash
distillrepo path/to/package --stdout
```

## What it Produces

### 1. LLM Bundle

The single-file bundle is optimized for copy-paste review in an LLM. Depending on mode, it can include:
- repo summary and review guidance
- inferred roots and top-level structure
- hotspot and cycle summaries
- selected full source
- summarized modules
- signature-only modules

Default output name:

```text
distilled.<package>.<MMMDDYYYY>.py
```

### 2. IR Directory

The `.distillrepo/` directory is the structured output for agents and tooling. It includes artifacts such as:
- `manifest.json`
- `repo_summary.md`
- `modules.json`
- `symbols.json`
- `relationships.json`
- `entrypoints.json`
- `chunks.json`
- `hotspots.json`
- `unused_candidates.json`

Use the IR when you want deterministic machine-readable structure instead of one monolithic bundle.

## How to Use the Outputs

For LLM review:
- start with `distilled.<package>.<date>.py`
- use `review` mode first unless you have a specific need
- if the bundle still feels too large, try `architecture` or `budgeted`
- if you need nearly raw source, use `concat` or `plain_concat`

For agents or scripts:
- read `.distillrepo/manifest.json` first
- use `modules.json`, `symbols.json`, and `relationships.json` to find relevant code
- use `chunks.json` and `hotspots.json` to prioritize what to inspect

For manual follow-up:
- use the bundle and IR as navigation aids, then verify important conclusions against the original source

## Review Modes

Recommended order:

- `review`
  Best default. Balanced mix of analysis, selected full source, summaries, and signatures.

- `architecture`
  Better when you want a high-level map of a repo before drilling into code.

- `hotspots`
  Better when you care most about complex or risky logic.

- `entrypath`
  Better when you want code closest to inferred runtime or review roots.

- `budgeted`
  More aggressive compression. Useful when context is tight and you still want a structured overview.

- `concat`
  Cleaned source concatenation with lightweight headers from static analysis. Useful when you want near-source input with basic structure preserved.

- `plain_concat`
  Cleaned source concatenation only. No added headers or analysis sections.

- `full`
  Largest review bundle. Includes the analysis sections plus broad full-source inclusion. Useful for debugging the tool or getting an almost-verbatim review artifact, not for tight context budgets.

## Common Scenarios

### First pass on an unfamiliar repo

```bash
distillrepo path/to/package
```

This uses `review` mode, which is the recommended default.

### Architecture walkthrough

```bash
distillrepo path/to/package --review-mode architecture
```

Use this when you want a compact map of the repo before asking the LLM deeper questions.

### Focus on risky or complex code

```bash
distillrepo path/to/package --review-mode hotspots
```

Useful for audit-style passes and targeted review.

### Near-source bundle with lightweight file markers

```bash
distillrepo path/to/package --review-mode concat
```

Useful when you want to preserve source fidelity but still keep file boundaries obvious.

### Source only, no added headers

```bash
distillrepo path/to/package --review-mode plain_concat
```

Useful when you want a cleaned source dump and nothing else.

### Override entry inference

```bash
distillrepo path/to/package \
  --entry-point-module cli.py \
  --entry-point-function main
```

Useful when the inferred root or entry surface is not the one you want reviewed.

### Tighten scope

```bash
distillrepo path/to/package \
  --exclude-dir tests \
  --exclude-glob "docs/*"
```

Useful when the repo has too much non-essential code for the task at hand.

## Stdout Summary

Each run prints a short summary so the user gets immediate value even before opening the outputs:
- files, symbols, and modules analyzed
- analysis kind
- roots analyzed
- reached vs not reached
- cycles
- possible unused symbol count
- top hotspot
- original vs distilled estimated tokens
- saved tokens, retained percentage, and compression ratio
- output paths

## What To Trust

`distillrepo` separates directly extracted facts from heuristic judgments.

High-confidence facts:
- file paths and module paths
- line spans and signatures
- declared symbols
- static imports
- directly resolved relationships when extraction succeeds

Heuristics:
- hotspot rankings
- importance scores
- root selection and pooled root coverage
- "not reached from roots" conclusions
- unused-code candidates
- source inclusion and compression decisions

"Not reached from roots" does not mean dead code. Dynamic imports, lazy exports, plugin registration, reflection, and runtime dispatch may be underrepresented.

## How It Chooses

`distillrepo` builds a small set of review roots, analyzes each root, then pools the results:
- application-style repos bias toward package root plus runnable entry surfaces
- library-style repos bias toward package and public subpackage roots
- shared-across-roots modules are ranked higher for review

The `.distillrepo/` IR keeps the fuller pooled analysis. The single-file `distilled.<package>.<date>.py` bundle is the review-oriented derived artifact.

## Compression Notes

The reported token counts are estimates based on text length. They are useful for comparing runs and spotting extreme compression, but they are not model-specific tokenizer counts.

There is not yet a universal compression threshold that guarantees trustworthy review quality across repos. Treat compression as an observed outcome, not the main objective. The main objective is retaining enough review-relevant structure and source to support a useful LLM review.


## Use of AI

This software was developed with the help of Codex model GPT 5.4.

