# Course Folder Rearrangement Pipeline

LLM-assisted reorganization of messy course directories into a clean, lecture-anchored structure. Given an input directory tree (as JSON) and a SQLite metadata DB containing per-file descriptions, the pipeline:

1. **Enriches** the tree with descriptions from the DB and prunes to the `study` category.
2. **Identifies the backbone** — the chronological folder (e.g. `study/slides/`) that anchors the course.
3. **Collects orphans** — every non-backbone item — and asks an LLM to assign each one to the most relevant backbone unit, with a `Lecture Miscellaneous` fallback.
4. **Refines miscellaneous** items into smaller logical clusters via a second LLM pass.
5. **Materializes** the resulting plan into a hierarchical JSON tree with file hashes for downstream consumption.

The end product is `outputs/<course>/[multi/]rearrangement_structure_tree.json`: a grouped, hash-stamped tree the rest of TAI can consume.

---

## Repository Layout

```
rearrange/
├── models.py          # Pydantic schemas + PipelineContext dataclass        
├── utils.py           # Console I/O, debug logs, path helpers, LLMGateway   
├── steps.py           # Domain logic — pre-merge → enrich → backbone →     
│                      #   orphans → plan
├── pipeline.py        # Orchestration, tree builder, CLI entry              
├── file_rearrang.py   # Back-compat re-export shim                          
│
├── input/             # Input tree JSONs and *_metadata.db files
├── outputs/<course>/  # Per-course outputs (enriched JSON, plan, tree)
│   └── debug/         # Numbered checkpoints (observability only — never read back)
└── README.md
```

### Module responsibilities

| Module | Contents |
|---|---|
| **`models.py`** | All Pydantic models (`BackboneGroup`, `OrphanMatch`, `OrphanMatchResponse`, `MiscRefinementResponse`, `RearrangedGroup`, …) and the frozen `PipelineContext` dataclass. Pure data, zero deps on the rest of the package. |
| **`utils.py`** | Cross-cutting infra: UTF-8 stdout reconfig, `_safe_print`, JSON loading, the `set_pipeline_log_dir` ContextVar + `save_debug_log`, path helpers (`_normalize_path`, `_is_under_path`, `_chunked`), course-name derivation, and the `LLMGateway` wrapper around OpenAI structured completions. |
| **`steps.py`** | The five sequential stages, each in its own labeled section: <br>**0. Pre-enrichment merge** — build / reorganize the input tree from team 1's first-pass tree + second-pass `final_paths` doc. <br>**1. Enrichment** — walk the tree, attach DB descriptions, prune to `study` (and `practice` when `--multi-match true`). <br>**2. Backbone identification** — ask the LLM to pick the chronological backbone folder. <br>**3. Orphan collection** — gather everything outside the backbone, with **task-instance** auto-aggregation (e.g. `Discussion_10/` collapses to one unit) and leaf-folder fallback. <br>**4. Plan generation** — merge backbone groups + matches and refine `Lecture Miscellaneous`. |
| **`pipeline.py`** | Top-level: `_build_context`, `run_enrichment`, `run_plan_matching`, the tree builder (`build_rearranged_structure_tree` and friends), and the CLI (`main`, `parse_cli_args`, `run_pipeline_cli`, `_step_*` dispatch). |
| **`file_rearrang.py`** | Re-exports the public surface so legacy `from file_rearrang import …` keeps working. |

---

## How the Pipeline Works

```
input/<tree>.json              input/<final_paths>.json     input/<course>_metadata.db
       (first-pass tree,          (second-pass routing,      (per-file file_hash
        from team 1)               from team 1, optional)     and description)
       │                                  │                          │
       └────────────┬─────────────────────┘                          │
                    ▼                                                 │
       ┌─────────────────────────────┐                               │
       │  Pre-enrichment merge       │   (if --final-paths supplied) │
       │   • build OR reorganize     │                               │
       │   • write v4_tree_          │                               │
       │     reorganized.json        │                               │
       └──────────────┬──────────────┘                               │
                      ▼                                              ▼
       ┌─────────────────────────────────────────────────────────────────┐
       │  enrich_structure_with_descriptions    step: enrich             │
       └──────────────┬──────────────────────────────────────────────────┘
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
       │     (task-instance + leaf   │
       │      auto-aggregation)      │
       │   • batch LLM matching      │
       │     (chunk_size = 50)       │
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
       │   (+ practice/support       │
       │    sections from reorg      │
       │    tree, when available)    │
       └──────────────┬──────────────┘
                      ▼
       outputs/<course>/rearrangement_structure_tree.json   ← FINAL ARTIFACT
```

### Step-by-step detail

**0. Pre-enrichment merge** (`steps.build_tree_from_final_paths` / `reorganize_tree_by_final_paths`)

When `--final-paths` is supplied, the input tree is constructed from team 1's second-pass routing **before** enrichment runs. Three modes:

| `--input` | `--final-paths` | What happens |
|---|---|---|
| ✓ | ✗ | Load tree directly from `--input` (legacy mode). |
| ✗ | ✓ | `build_tree_from_final_paths`: build a fresh tree where every file lives at its `final_path`. No first-pass file metadata available. |
| ✓ | ✓ | `reorganize_tree_by_final_paths`: walk team 1's second-pass entries; carry `file_hash` and other first-pass metadata over when the source matches; create placeholder nodes (flagged `"missing_from_first_pass": true`) for entries that team 1 classified but the first-pass tree didn't include. |

In both `--final-paths` modes, the rebuilt tree is also persisted to `outputs/<course>/v4_tree_reorganized.json` for inspection. Folders are auto-marked `by_sequence: True` when **at least two of their child folders each represent a single distinct sequence step** (e.g. `study/slides/` whose children `01..21` each carry one sequence_name). Leaf folders containing files of one step are *not* marked.

**1. Enrich** (`steps.enrich_structure_with_descriptions`)
- Walks the input tree (or the in-memory pre-merged tree if step 0 ran).
- For each file, looks up a description in the metadata DB — exact `file_name =` match first, falling back to a wildcard-escaped `LIKE '%/<filename>'` query (so filenames like `lecture_01.pdf` containing `_` aren't treated as wildcards).
- Skips `.yaml`/`.json` config files.
- Filters to nodes with category `study`. With `--multi-match true`, the `practice` subtree is merged into `study` and its paths rebased.
- Carries `final_path`, `task_name`, `sequence_name`, and `source` (original on-disk path) through onto file nodes when present.

**2. Backbone** (`steps.run_backbone_identification`)
- Extracts every file description from the enriched tree and asks `gpt-5-mini` to pick the single folder that "best serves as the chronological backbone."
- Returns a path like `study/slides`.

**3. Plan matching** (`pipeline.run_plan_matching`)
- Builds one `BackboneGroup` per immediate child of the backbone folder, plus a sentinel `Lecture Miscellaneous` group.
- Collects orphans (everything not under the backbone) with two-tier auto-aggregation:
  - **Task-instance aggregation** (`_orphan_task_instance_folder_auto_aggregate`): a non-leaf folder is collapsed into a single `folder_unit` orphan when it represents one task instance — its descendant files share a single `(task_name, sequence_name)` matching the folder name, and the folder isn't a sequence container itself. Example: `study/discussion/Discussion_10/` (containing both `Discussion 10.html` and `sol-disc10/disc10.pdf`) becomes one orphan instead of being split.
  - **Leaf-folder aggregation** (`_orphan_leaf_folder_auto_aggregate`): a folder with files only and no subfolders becomes a single unit (legacy behavior, still applies when team-1 task/sequence metadata isn't present).
- Sends orphans to the LLM in batches of 50, with a different system prompt per `--multi-match` value (single-best-group vs. all-relevant-groups).
- Filters hallucinated paths from LLM output. Three rescue tiers: exact match, whitespace-normalized match, and **basename rescue** (when the LLM drops/adds intermediate folders but the leaf name maps to exactly one orphan). Ambiguous-basename and truly hallucinated paths are dropped with deduplicated warnings.
- Falls back any unmatched orphan into `Lecture Miscellaneous`.
- `generate_rearrangement_plan` then merges everything and asks the LLM to refine `Lecture Miscellaneous` into specific clusters (e.g. `Review`, `Tutorials`, `Administrivia`).

**4. Tree** (`pipeline.build_rearranged_structure_tree`)
- Loads the plan and enriched tree, builds the hash map from the metadata DB.
- For each group in the plan, resolves each item path (with a basename fallback for path mismatches) and rebuilds the subtree with `file_hash` populated for every file.
- Wraps all study content under a top-level `Study` group.
- When the reorganized tree is available (built from `--final-paths`), appends top-level `Practice` and `Support` groups assembled from that tree, so the final artifact spans the full course rather than just the study subset.

---

## Quick Start

### Install

```bash
pip install openai pydantic python-dotenv
```

Set your OpenAI key (a `.env` file in this directory works — `python-dotenv` loads it):

```env
OPENAI_API_KEY=sk-...
```

### Inputs

Drop the following into `input/`:

| File | Required? | Purpose |
|---|---|---|
| **Tree JSON** (e.g. `bfs_v4_tree.json`) | yes (or pass `--final-paths` instead) | Directory tree from the upstream walker. Each file node carries `path`, `name`, `category`, `file_hash`. |
| **Metadata DB** (e.g. `CS 61A_metadata_NewPT.db`) | yes | SQLite file with a `file` table providing `file_name`, `relative_path`, `description`, `file_hash`. |
| **Final-paths JSON** (e.g. `bfs_v4_final_paths.json`) | optional | Team 1's second-pass classification doc (entries: `source`, `final_path`, `category`, `task_name`, `sequence_name`, `category_depth`). When supplied, the tree is reorganized to match team 1's routing before enrichment. |

The DB filename is auto-detected if exactly one `*_metadata.db` exists in `input/`.

### Run the full pipeline (default)

```bash
# Recommended: both files supplied → reorganize, enrich, backbone, match, tree.
python pipeline.py \
    --input bfs_v4_tree.json \
    --db "CS 61A_metadata_NewPT.db" \
    --final-paths bfs_v4_final_paths.json \
    --course cs61a \
    --multi-match true
```

`--step all` is the default, so it can be omitted. The final artifact is `outputs/<course>/multi/rearrangement_structure_tree.json` (or `outputs/<course>/rearrangement_structure_tree.json` when `--multi-match false`).

#### Variants

```bash
# Final-paths alone (no first-pass tree — build from scratch):
python pipeline.py --db "CS 61A_metadata_NewPT.db" --final-paths bfs_v4_final_paths.json --course cs61a

# First-pass tree alone (legacy — no team-1 routing):
python pipeline.py --input bfs_v4_tree.json --db "CS 61A_metadata_NewPT.db" --course cs61a

# Single-best-group instead of multi-target matching:
python pipeline.py --input bfs_v4_tree.json --db "..." --final-paths bfs_v4_final_paths.json --course cs61a --multi-match false
```

### Run a single step

```bash
python pipeline.py --step enrich   --input <tree>.json --db <db>.db --final-paths <fp>.json --course <course>
python pipeline.py --step backbone --course <course>
python pipeline.py --step match    --course <course>
python pipeline.py --step tree     --course <course> --db <db>.db --final-paths <fp>.json
```

Each step reads the canonical artifact from the previous step's `outputs/<course>/` folder. ⚠️ **For `--step tree` to include the `Practice` and `Support` top-level groups, you must re-pass `--final-paths` (and `--input` if you used it during enrich)** — the tree builder reconstructs the reorganized tree from those inputs rather than reading the persisted `v4_tree_reorganized.json`. Without them, the final artifact contains only the `Study` group.

Numbered checkpoint files in `outputs/<course>/debug/` are written for observability and are **never read back as input**.

---

## CLI Reference

```
python pipeline.py [--step STEP] [--input FILE] [--db FILE] [--course NAME]
                   [--multi-match {true,false}] [--final-paths FILE]
```

| Flag | Default | Meaning |
|---|---|---|
| `--step` | `all` | One of `enrich`, `backbone`, `match`, `tree`, `all`. `all` runs the full pipeline end-to-end. |
| `--input` | _none_ | First-pass tree JSON inside `input/` or absolute path. **Required for `enrich` / `all` when `--final-paths` is not supplied.** Combined with `--final-paths` to enable the reorganize path that preserves first-pass `file_hash`. |
| `--db` | _auto-detect_ | Metadata DB filename inside `input/` or absolute path. Auto-detected when exactly one `*_metadata.db` exists in `input/`. |
| `--course` | _auto-derived_ | Course identifier; controls `outputs/<course>/`. Derived from `--db` or `--input` when omitted (e.g. `CS 61A_metadata.db` → `CS_61A`). |
| `--multi-match {true,false}` | `true` | Whether orphans may map to multiple backbone groups. `true` enables multi-target matching (best for files spanning topics) and writes results under `outputs/<course>/multi/`; `false` forces single-best-group placement under `outputs/<course>/`. |
| `--final-paths` | _none_ | Team-1 second-pass classification JSON (filename inside `input/` or absolute path). Used together with `--input` to **reorganize** the first-pass tree, or alone to **build** a tree from scratch. The result is persisted to `outputs/<course>/v4_tree_reorganized.json`. |

Accepted values for `--multi-match`: `true/false`, `t/f`, `yes/no`, `y/n`, `1/0` (case-insensitive).

`file_rearrang.py` is a back-compat shim that re-exports everything from the four implementation modules. `python file_rearrang.py --help` works identically to `python pipeline.py --help`.

---

## Outputs

Everything for one course lives under a single folder: `outputs/<course>/` (single-best-group) or `outputs/<course>/multi/` (multi-match).

```
outputs/<course>[/multi]/
├── v4_tree_reorganized.json             ← enrich (only when --final-paths is passed)
├── study_enriched.json                  ← enrich
├── backbone_result.json                 ← backbone
├── orphan_matches.json                  ← match
├── rearrangement_plan.json              ← match
├── rearrangement_structure_tree.json    ← tree (final artifact)
└── debug/                               ← numbered checkpoints (observability only)
    ├── 01_backbone_descriptions_payload.json
    ├── 02_1_backbone_subtree.json
    ├── 02_2_backbone_groups.json
    ├── 02_3_orphans_collected.json
    └── 06_pre_refinement_plan.json
```

| Artifact | Source step | Purpose |
|---|---|---|
| `v4_tree_reorganized.json` | `enrich` (only with `--final-paths`) | Tree rebuilt so every file lives at its team-1 `final_path` location. Files team 1 classified but the first-pass tree omitted appear as placeholder nodes. Saved for inspection. |
| `study_enriched.json` | `enrich` | Input tree pruned to `study`, with DB descriptions attached (and team-1 fields if merged). Consumed by `backbone` / `match` / `tree`. |
| `backbone_result.json` | `backbone` | `{"backbone_path": "study/Lecture Slides"}`. Consumed by `match`. |
| `orphan_matches.json` | `match` | Every orphan → assigned group mapping. |
| `rearrangement_plan.json` | `match` | Grouped plan — list of `{group_name, main_item, related_items, description}`. Consumed by `tree`. |
| `rearrangement_structure_tree.json` | `tree` | **Final artifact.** Top-level `Study` group always present; `Practice` / `Support` groups appended when team-1 final-paths data is available at tree-step time. All file nodes carry `file_hash`. |
| `debug/*.json` | various | Pipeline checkpoints. Inspect to debug LLM behavior; never read back by the pipeline itself. |

The `debug/` directory is bound to the running task via a `ContextVar` (`set_pipeline_log_dir`), so concurrent runs against different courses don't cross-contaminate.

---

## Programmatic Use

The pipeline is a library, not just a CLI. Typical embedded usage:

```python
from pipeline import run_enrichment, run_plan_matching
from steps import run_backbone_identification
from utils import LLMGateway, load_json_file, set_pipeline_log_dir, reset_pipeline_log_dir

# 1. Enrich (with team-1 final-paths reorganization)
enriched_path = run_enrichment(
    base_dir="/path/to/rearrange",
    input_filename="bfs_v4_tree.json",
    db_filename="CS 61A_metadata_NewPT.db",
    course_name="cs61a/multi",
    multi_match=True,
    final_paths_filename="bfs_v4_final_paths.json",
)

# 2. Backbone + 3. Match
enriched_data = load_json_file(enriched_path)
gateway = LLMGateway()  # or inject a custom OpenAI client

token = set_pipeline_log_dir("/path/to/rearrange/outputs/cs61a/multi/debug")
try:
    backbone_path = run_backbone_identification(enriched_data, llm_gateway=gateway)
    matches, plan = run_plan_matching(
        enriched_data, backbone_path, multi_match=True, llm_gateway=gateway
    )
finally:
    reset_pipeline_log_dir(token)
```

`LLMGateway` accepts a pre-configured `openai.OpenAI()` client — useful for testing with a stub.

---

## Design Notes

### Path semantics

Two equivalent path-containment checks were a source of subtle bugs (e.g. `study/lec` matched `study/lecture`). The codebase uses one helper consistently:

```python
from utils import _is_under_path, _normalize_path

_is_under_path("study/lec/01", "study/lec")   # True
_is_under_path("study/lecture", "study/lec")  # False — slash boundary required
```

Any new path-containment code should reuse `_is_under_path`. Avoid raw `path.startswith(other)`.

All path joining goes through `pathlib.Path` (zero `os.path.join` calls in the four core modules) for cross-platform safety.

### Safe DB description lookup

`enrich_structure_with_descriptions` uses an exact-name match first and only falls back to a wildcard-escaped `LIKE` anchored to `/<filename>`. This avoids false positives when filenames contain `_` or `%` (which are wildcards in raw `LIKE`).

### Orphan aggregation rules

The orphan collector applies aggregation in this order, returning early on the first match:

1. **Task-instance** (`_orphan_task_instance_folder_auto_aggregate`) — folder represents one task instance: descendants share a single `(task_name, sequence_name)` matching the folder name, and it's not itself a sequence container. Catches cases like `Discussion_10/{Discussion 10.html, sol-disc10/disc10.pdf}` → one unit.
2. **Leaf-folder** (`_orphan_leaf_folder_auto_aggregate`) — folder has files only, no subfolders, not a sequence container. Catches cases like `support/administrative/{file1, file2, ...}` → one unit.
3. **Manual aggregate** (`_orphan_append_manual_aggregate`) — explicit list passed in by caller (currently unused; stays for callers that want to override).
4. **Recurse into subfolders** otherwise.

Sequence containers (`by_sequence: True`) are *never* auto-aggregated — their children are individually evaluated so each sequence step becomes its own unit.

### LLM hallucination defenses

`_filter_matches` runs three rescue passes per LLM-returned `item_path`:

1. Exact match against any orphan's `relative_path` or `structure_path`.
2. Whitespace-normalized match.
3. Basename rescue: if the LLM dropped/added intermediate folders but the leaf name maps to exactly one orphan, rewrite the path.

Ambiguous basenames (multiple orphans with the same leaf name) and truly unknown paths are dropped, with one warning per unique LLM-returned path (no duplicate spam from `--multi-match true` runs).

### Plan persistence

The rearrangement plan is a first-class artifact at `outputs/<course>/rearrangement_plan.json`. The `tree` step reads only this canonical path. Numbered files in `outputs/<course>/debug/` are observability checkpoints — the pipeline never consumes them as input.

### Models

- **`gpt-5-mini`** for backbone identification, orphan matching, and miscellaneous refinement.
- All calls use `seed=42` for determinism; the structured-output path (`client.beta.chat.completions.parse`) enforces Pydantic schemas.

---

## Known Gaps

- **No automated tests.** Recent additions (`_orphan_task_instance_folder_auto_aggregate`, basename rescue, the build/reorganize merge logic, the `Practice`/`Support` append in the tree builder) all lack regression coverage. Adding `tests/` is the highest-priority follow-up.
- **`--step tree` standalone may omit Practice/Support groups.** The tree builder reconstructs the reorganized tree from inputs rather than reading the persisted `v4_tree_reorganized.json`. If you re-run `--step tree` without re-passing `--final-paths` (and `--input`), only the `Study` group is in the final artifact. Workaround: always re-pass the same flags, or run `--step all`.
- **`run_file_rearrangement` is a stub.** Actually moving files on disk per the plan is not yet implemented.
- **`get_folder_candidates` and the commented-out `run_aggregation_analysis`** are dead code from an earlier experiment. Safe to delete.
- **Logging is `print`-based.** Migrating to the standard `logging` module is on the wishlist.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `FileNotFoundError: No *_metadata.db file found` | Drop your DB into `input/` or pass `--db`. |
| `Backbone folder '...' not found in enriched data` | The backbone path saved by step `backbone` doesn't exist in the enriched tree. Re-run `enrich` and `backbone`. |
| `Warning: Filtered out hallucinated item: ...` | The LLM invented a path that wasn't in the orphan list. The path is dropped from matches; no action needed. |
| `Warning: Ambiguous basename '...' matches N orphans, dropping: ...` | The LLM returned a leaf name that maps to multiple orphans. Surface a more discriminating description in the orphan if this matters. |
| `Warning: Item not found in enriched data: ...` (during `tree`) | Plan references an item missing from the enriched tree. The tree builder writes a `type: unknown` placeholder so downstream code can decide how to handle it. |
| Final structure tree only contains `Study` (no `Practice`/`Support`) | You ran `--step tree` without re-passing `--final-paths`. Re-run with the same flags as the original `enrich` call. |
| `[reorg] Placed N files (... placeholders for files team 1 classified but the first-pass tree omitted)` | Team 1's first-pass tree is missing some files that their second pass classified. The placeholders will get hashes filled in by name-based DB lookup later. Treat this as a data-quality signal worth flagging back to team 1. |
| Garbled non-ASCII in console output | `_configure_stdout()` tries to switch the console to UTF-8 at import; if it can't, `_safe_print` falls back to replacement characters rather than crashing. |


---

## Next Step: DB Mapping Orchestrator (`map_to_db_v2.py`)

The sections above document the upstream course-folder rearrangement pipeline. `map_to_db_v2.py` is a separate downstream step: it writes reorganized course paths back into each course metadata SQLite database after the rearrangement plan and groundtruth folders exist.

This script does not merge all course-specific matching rules into one generic implementation. Instead, it delegates to the per-course scripts:

| Course | Delegated script | Metadata DB |
|---|---|---|
| CS61A | `61A/reorganization_v3.py` | `61A/metadata/CS 61A_metadata.db` |
| EECS106B | `106B/reorganization.py` | `106B/metadata/EECS_106B_metadata.db` |

This separation matters because CS61A and EECS106B intentionally differ in row-cardinality rules, UUID handling, fallback behavior, and duplicate path policy.

### What it does

- Loads the requested course implementation from this repository.
- Overrides the delegated script's `NEW_TABLE` with `--output-table` (default: `file_new`).
- Supports a dry run that builds mappings in memory and prints match statistics without writing to SQLite.
- Creates a timestamped database backup before a real write.
- Optionally drops nonessential tables after a successful write via `--cleanup`.
- For CS61A only, can optionally materialize the physical/symlink logical output tree via `--create-logical-output`.

### Basic usage

Run commands from the repository root:

```bash
python map_to_db_v2.py --course cs61a --dry-run
python map_to_db_v2.py --course eecs106b --dry-run
```

Write a new output table:

```bash
python map_to_db_v2.py --course cs61a --output-table file_new
python map_to_db_v2.py --course eecs106b --output-table file_new
```

Write and then remove all tables except `file`, `problem`, `chunks`, and the output table:

```bash
python map_to_db_v2.py --course cs61a --output-table file_new --cleanup
python map_to_db_v2.py --course eecs106b --output-table file_new --cleanup
```

For CS61A, also create the logical output tree:

```bash
python map_to_db_v2.py --course cs61a --output-table file_new --create-logical-output
```

### CLI reference

| Flag | Required | Meaning |
|---|---:|---|
| `--course` | Yes | Course selector. Accepted values include `cs61a`, `61a`, `eecs106b`, and `106b`. |
| `--output-table` | No | SQLite table to create. Defaults to `file_new`. |
| `--dry-run` | No | Build mappings and print statistics without changing the DB. |
| `--cleanup` | No | After a successful write, drop tables other than `file`, `problem`, `chunks`, and the output table. |
| `--create-logical-output` | No | CS61A only. Also creates the delegated script's logical output tree. |

### Dry-run output

CS61A reports:

- old row count from the original `file` table
- matched source file count before and after duplicate logical-path resolution
- expanded output row count
- duplicate `logical_path` count after resolution
- `original/...` fallback row count

EECS106B reports:

- old row count and output row count
- source-type counts for `llm_json`, `practice_support_gt`, and fallback rows
- top match reasons
- duplicate `logical_path` summary
- the projected path column name (`file_path` when the delegated script is in `DEVELOP_MODE = "down"`)

### Safety notes

- Dry runs are read-only and are the recommended first step before writing.
- Real writes create a database backup named like `*.backup_before_<output_table>_<timestamp>.db`.
- EECS106B's delegated script also creates its own backup during `main()`, so a full EECS106B write may produce two backup files.
- `--cleanup` is destructive after the new table is created. Use it only when the extra tables are disposable.
- EECS106B may report duplicate logical paths in dry-run output. That is tolerated by the delegated EECS106B writer because it preserves one output row per old DB row and does not create a unique path index.

### Recent dry-run baseline

From the current repository layout, the dry runs completed with these headline counts:

| Course | Old rows | Output rows | Fallback rows | Duplicate logical paths |
|---|---:|---:|---:|---:|
| CS61A | 948 | 2000 | 153 | 0 |
| EECS106B | 802 | 802 | 27 | 69 |
