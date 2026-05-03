# Course Folder Rearrangement Pipeline

LLM-assisted reorganization of messy course directories into a clean, lecture-anchored structure. Given a directory tree (as JSON) and a SQLite metadata DB containing per-file descriptions, the pipeline:

1. **Enriches** the tree with descriptions from the DB (filtered to the `study` category).
2. **Identifies the backbone** — the chronological folder (e.g. `Lecture Slides/`) that anchors the course.
3. **Collects orphans** — every non-backbone item — and asks an LLM to assign each one to the most relevant backbone unit, with a `Lecture Miscellaneous` fallback.
4. **Refines miscellaneous** items into smaller logical clusters via a second LLM pass.
5. **Materializes** the resulting plan into a hierarchical JSON tree with file hashes for downstream consumption.

The end product is `outputs/<course>/rearrangement_structure_tree.json`: a grouped, hash-stamped tree the rest of TAI can use.

---

## Repository Layout

```
rearrange/
├── models.py          # Pydantic schemas + PipelineContext dataclass        
├── utils.py           # Console I/O, debug logs, path helpers, LLMGateway   
├── steps.py           # Enrich → backbone → orphans → plan (domain logic)   
├── pipeline.py        # Orchestration, tree builder, CLI entry              
├── file_rearrang.py   # Back-compat re-export shim                          
│
├── input/             # Input tree JSONs and *_metadata.db files
├── outputs/<course>/  # Per-course outputs (enriched JSON, plan, tree)
├── logs/<course>/     # Per-course numbered debug logs
└── README.md
```

### Module responsibilities

| Module | Contents |
|---|---|
| **`models.py`** | All Pydantic models (`BackboneGroup`, `OrphanMatch`, `OrphanMatchResponse`, `MiscRefinementResponse`, `RearrangedGroup`, …) and the frozen `PipelineContext` dataclass. Pure data, zero deps on the rest of the package. |
| **`utils.py`** | Cross-cutting infra: UTF-8 stdout reconfig, `_safe_print`, JSON loading, the `set_pipeline_log_dir` ContextVar + `save_debug_log`, path helpers (`_normalize_path`, `_is_under_path`, `_chunked`), course-name derivation, and the `LLMGateway` wrapper around OpenAI structured completions. |
| **`steps.py`** | The four sequential LLM-driven stages, each in its own labeled section: <br>1. **Enrichment** — walks the input tree, attaches DB descriptions, filters to `study` (and `practice` when `--multi-match`). <br>2. **Backbone identification** — asks the LLM to pick the chronological backbone folder. <br>3. **Orphan collection** — gathers everything outside the backbone, with leaf-folder auto-aggregation. <br>4. **Plan generation** — merges backbone groups + matches and refines `Lecture Miscellaneous`. |
| **`pipeline.py`** | Top-level: `_build_context`, `run_enrichment`, `run_plan_matching`, the tree builder (`build_rearranged_structure_tree` and friends), and the CLI (`main`, `parse_cli_args`, `run_pipeline_cli`, `run_tree_step`). |
| **`file_rearrang.py`** | Re-exports the public surface from the four modules above so legacy `from file_rearrang import …` still works. |

---

## How the Pipeline Works

```
input/<tree>.json + input/<course>_metadata.db
                     │
                     ▼
       ┌─────────────────────────────┐
       │  enrich_structure_with_     │   step: enrich
       │  descriptions               │
       └──────────────┬──────────────┘
                      ▼
       outputs/<course>/study_enriched.json
                      │
                      ▼
       ┌─────────────────────────────┐
       │  run_backbone_              │   step: backbone
       │  identification (LLM)       │
       └──────────────┬──────────────┘
                      ▼
       outputs/<course>/backbone_result.json
                      │
                      ▼
       ┌─────────────────────────────┐
       │  run_plan_matching:         │   step: match
       │   • collect_orphan_items    │
       │   • batch LLM matching      │
       │   • generate_rearrangement_ │
       │     plan (+ misc refine)    │
       └──────────────┬──────────────┘
                      ▼
       outputs/<course>/orphan_matches.json
       outputs/<course>/rearrangement_plan.json
                      │
                      ▼
       ┌─────────────────────────────┐
       │  build_rearranged_          │   step: tree
       │  structure_tree             │
       └──────────────┬──────────────┘
                      ▼
       outputs/<course>/rearrangement_structure_tree.json
```

### Step-by-step detail

**1. Enrich (`steps.enrich_structure_with_descriptions`)**
- Reads the raw tree JSON. Walks every node.
- For each file, looks up a description in the metadata DB, preferring an exact `file_name =` match and falling back to a `LIKE '%/<filename>'` query (with `%`/`_` properly escaped — filenames like `lecture_01.pdf` are common).
- Skips `.yaml`/`.json` config files.
- Filters to nodes whose category is `study`. With `--multi-match`, the `practice` subtree is merged into `study` and its paths rebased.
- Maps the legacy `root` path prefix to `study`.

**2. Backbone (`steps.run_backbone_identification`)**
- Extracts every file description from the enriched tree and asks `gpt-5-mini` for the single folder that "best serves as the chronological backbone."
- Returns a path like `study/Lecture Slides`.

**3. Plan matching (`pipeline.run_plan_matching`)**
- Builds one `BackboneGroup` per immediate child of the backbone folder, plus a sentinel `Lecture Miscellaneous` group.
- Collects orphans: everything not under the backbone. Leaf folders with files-only and not marked `by_sequence` are auto-aggregated into a single `folder_unit` so a `Project 1/` folder with `instructions.pdf + starter.py + solution.py` stays cohesive.
- Sends orphans to the LLM in batches of 50, with a different system prompt per `--multi-match` mode (single-best-group vs. all-relevant-groups).
- Filters hallucinated paths from LLM output and falls back any unmatched orphan into `Lecture Miscellaneous`.
- `generate_rearrangement_plan` then merges everything and asks the LLM to refine `Lecture Miscellaneous` into specific clusters (e.g. `Review`, `Tutorials`, `Administrivia`).

**4. Tree (`pipeline.build_rearranged_structure_tree`)**
- Loads the plan, the enriched tree, and the file-hash map from the DB.
- For each group in the plan, resolves each item path (with a basename fallback for path mismatches) and rebuilds the subtree with `file_hash` populated for every file.
- Writes `rearrangement_structure_tree.json` — a list of group nodes ready for downstream consumption.

---

## Quick Start

### Install

```bash
pip install openai pydantic python-dotenv
```

Set your OpenAI key (an `.env` file at this directory works — `python-dotenv` loads it):

```env
OPENAI_API_KEY=sk-...
```

### Inputs

Drop two files into `input/`:

- A directory-tree JSON (e.g. `bfs_v4_tree_cs61a.json`) produced by an upstream walker.
- The matching metadata SQLite DB (e.g. `CS 61A_metadata_NewPT.db`) with a `file` table containing at minimum `file_name`, `relative_path`, `description`, `file_hash`.

The DB filename is auto-detected if exactly one `*_metadata.db` exists in `input/`.

### Run the full pipeline

```bash
# Enrich → backbone → match (writes outputs + logs under outputs/<course>/, logs/<course>/)
python pipeline.py --step all --input <tree>.json --db <metadata>.db --course <course> --multi-match <true/false>

# Then materialize the tree
python pipeline.py --step tree --course <course>
```

### Run a single step

```bash
python pipeline.py --step enrich   --input <tree>.json --db <metadata>.db --course <course>
python pipeline.py --step backbone --course <course>
python pipeline.py --step match    --course <course>
python pipeline.py --step tree     --course <course>
```

Each step reads its predecessor's canonical output from `outputs/<course>/`. Steps can be re-run independently. **Logs in `logs/<course>/` are observational checkpoints only — the pipeline never reads them as input.**

---

## CLI Reference

```
python pipeline.py [--step STEP] [--input FILE] [--db FILE] [--course NAME] [--multi-match | --no-multi-match]
```

| Flag | Default | Meaning |
|---|---|---|
| `--step` | `enrich` | One of `enrich`, `backbone`, `match`, `all`, `tree`. |
| `--input` | _none_ | Tree JSON filename inside `input/`, or an absolute path. **Required for `enrich` / `all`.** Ignored by other steps. |
| `--db` | _none_ | Metadata DB filename inside `input/`, or an absolute path. Auto-detected when exactly one `*_metadata.db` exists in `input/`. |
| `--course` | _auto-derived_ | Course identifier; controls `outputs/<course>/` and `logs/<course>/`. Derived from `--db` or `--input` when omitted (e.g. `CS 61A_metadata.db` → `CS_61A`). |
| `--multi-match` / `--no-multi-match` | `--multi-match` | When enabled, an orphan may be assigned to multiple backbone units (best for files that span topics). Disable for single-best-group placement. |

When `--multi-match` is enabled, results are written under `outputs/<course>/multi/` and `logs/<course>/multi/` so single-vs-multi runs don't clobber each other.

`file_rearrang.py` is a back-compat shim that re-exports everything from the four implementation modules. `python file_rearrang.py --help` works identically to `python pipeline.py --help`.

---

## Outputs

Per course, written to `outputs/<course>/` (or `outputs/<course>/multi/`):

| File | Source step | Contents |
|---|---|---|
| `study_enriched.json` | `enrich` | The input tree pruned to `study`, with DB descriptions attached. |
| `backbone_result.json` | `backbone` | `{"backbone_path": "study/Lecture Slides"}`. |
| `orphan_matches.json` | `match` | Every orphan → assigned group mapping. |
| `rearrangement_plan.json` | `match` | The grouped plan — list of `{group_name, main_item, related_items, description}`. |
| `rearrangement_structure_tree.json` | `tree` | Final hierarchical tree with `file_hash` populated. |

Numbered debug logs land in `logs/<course>/`:

```
01_backbone_descriptions_payload.json   01_backbone_path.json
02_1_backbone_subtree.json              02_2_backbone_groups.json
02_3_orphans_collected.json             03_groups_for_matching.json
04_orphans.json                         06_pre_refinement_plan.json
07_rearrangement_plan.json
```

Logs are bound to the running task via a `ContextVar` (`set_pipeline_log_dir`) so concurrent runs against different courses don't cross-contaminate.

---

## Programmatic Use

The pipeline is a library, not just a CLI. Typical embedded usage:

```python
from pipeline import run_enrichment, run_plan_matching
from steps import run_backbone_identification
from utils import LLMGateway, load_json_file, set_pipeline_log_dir

# 1. Enrich
enriched_path = run_enrichment(
    base_dir="/path/to/rearrange",
    input_filename="bfs_v4_tree_cs61a.json",
    db_filename="CS 61A_metadata_NewPT.db",
    course_name="cs61a",
    multi_match=True,
)

# 2. Backbone + 3. Match
enriched_data = load_json_file(enriched_path)
gateway = LLMGateway()  # or inject a custom OpenAI client

token = set_pipeline_log_dir("/path/to/rearrange/logs/cs61a/multi")
try:
    backbone_path = run_backbone_identification(enriched_data, llm_gateway=gateway)
    matches, plan = run_plan_matching(
        enriched_data, backbone_path, multi_match=True, llm_gateway=gateway
    )
finally:
    from utils import reset_pipeline_log_dir
    reset_pipeline_log_dir(token)
```

`LLMGateway` accepts a pre-configured `openai.OpenAI()` client — useful for testing with a stub.

---

## Design Notes

### Path semantics

Two equivalent path-containment checks were a source of subtle bugs (e.g. `study/lec` matched `study/lecture`). The codebase now uses one helper consistently:

```python
from utils import _is_under_path, _normalize_path

_is_under_path("study/lec/01", "study/lec")  # True
_is_under_path("study/lecture", "study/lec") # False — slash boundary required
```

Any new path-containment code should reuse `_is_under_path`. Avoid raw `path.startswith(other)`.

### Safe DB description lookup

`steps.enrich_structure_with_descriptions` uses an exact-name match first and only falls back to a wildcard-escaped `LIKE` anchored to `/<filename>`. This avoids false positives when filenames contain `_` or `%` (treated as wildcards in raw `LIKE`).

### Plan persistence

The rearrangement plan is a first-class artifact written to `outputs/<course>/rearrangement_plan.json`. The `tree` step reads only this canonical path. Numbered files in `logs/<course>/` (e.g. `07_rearrangement_plan.json`) are observability checkpoints — the pipeline never consumes them as input.

### Models

- **gpt-5-mini** for backbone identification, orphan matching, and miscellaneous refinement.
- All calls use `seed=42` for determinism; the structured-output path (`client.beta.chat.completions.parse`) enforces Pydantic schemas.

---

## Known Gaps

- **No automated tests.** Recent bug fixes (path-prefix in `extract_backbone_subtree`, SQL `LIKE` wildcard escaping in `get_file_description`) have no regression coverage. Adding `tests/` is the highest-priority follow-up.
- **`run_file_rearrangement` is a stub.** Actually moving files on disk per the plan is not yet implemented.
- **Aggregation-analysis LLM step** is commented out (search `run_aggregation_analysis` in git history). Currently the pipeline relies only on the leaf-folder heuristic to decide whether to keep a folder as a single unit.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `FileNotFoundError: No *_metadata.db file found` | Drop your DB into `input/` or pass `--db`. |
| `Backbone folder '...' not found in enriched data` | The backbone path saved by step `backbone` doesn't exist in the enriched tree. Re-run `enrich` and `backbone`. |
| `Warning: Filtered out hallucinated item: ...` | The LLM invented a path that wasn't in the orphan list. The path is dropped from matches; no action needed. |
| `Warning: Item not found in enriched data: ...` (during `tree`) | Plan references an item missing from the enriched tree. The tree builder writes a `type: unknown` placeholder so downstream code can decide how to handle it. |
| Garbled non-ASCII in console output | `_configure_stdout()` tries to switch the console to UTF-8 at import; if it can't, `_safe_print` falls back to replacement characters rather than crashing. |

---

## Related Files in This Directory

- `deep_reorg.py`, `dir_to_json.py`, `folder_to_Json.py` — sibling utilities that produce input trees. Not part of this pipeline; documented elsewhere.
- `file_rearrang copy.py` — pre-refactor snapshot of the original 1894-line monolith. Safe to delete once you're confident in the new structure.
