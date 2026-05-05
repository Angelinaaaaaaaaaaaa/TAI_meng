# TAI MEng: Course Repository Reorganization Pipeline

This repository contains our MEng capstone implementation for **content-aware course file reorganization** in support of Berkeley Teaching AI (TAI). The system converts fragmented, format-based course repositories into a cleaner, more pedagogically meaningful structure that is easier for both **students** and **retrieval-based AI systems** to navigate.

The pipeline is organized into two major stages:

1. **Step 1 — File Categorization** (`bfs_v4.py` + `classify_v4.py`)  
   Reorganizes raw course materials into three educational roles: **study**, **practice**, and **support** using a **hierarchical breadth-first traversal** and **LLM-based classification**.

2. **Step 2 — Lecture Labeling / Rearrangement** (`pipeline.py` + `steps.py` + `models.py` + `utils.py`)  
   Takes the reorganized `study` materials and further groups them by **lecture/topic units** using a **Plan-and-Solve style** LLM pipeline.

A downstream utility, `map_to_db_v2.py`, writes reorganized logical paths back into course metadata databases for later use by TAI.

---

## Why this project exists

Modern course content is often spread across course websites, discussion pages, video platforms, homework portals, and ad hoc uploaded assets. Even when all files are collected into one repository, the resulting structure is usually inconsistent and difficult to browse. This hurts:

- **students**, who spend time searching for the right lecture, discussion, homework, or lab materials; and
- **educational AI systems**, especially retrieval-augmented systems, which rely on well-structured source material.

Our capstone addresses this infrastructure problem by reorganizing course repositories into a structure that better reflects how students actually study: by **educational role**, **task**, and **lecture/topic progression**.

---

## Repository overview

Current top-level files in the repository include:

- `bfs_v4.py` — Step 1 traversal and categorization pipeline
- `classify_v4.py` — Step 1 LLM classifier and task/sequence extraction logic
- `pipeline.py` — Step 2 orchestration and CLI
- `steps.py` — Step 2 backbone detection, orphan collection, matching, and plan generation
- `models.py` — shared schemas and pipeline context models
- `utils.py` — logging, JSON helpers, path utilities, OpenAI gateway
- `map_to_db_v2.py` — downstream DB mapping orchestrator
- `file_rearrang.py` — compatibility re-export shim for the Step 2 pipeline
- `bfs_v3.py`, `classify_v3.py` — earlier Step 1 versions kept for reference
- `second_pass_organizer.py`, `PSEUDOCODE.py` — auxiliary or legacy files

---

# End-to-end system architecture

```text
Raw course repository
        │
        ▼
Step 1: Hierarchical BFS categorization
(bfs_v4.py + classify_v4.py)
        │
        ├── study/
        ├── practice/
        └── support/
        │
        ▼
Step 2: Lecture labeling / study rearrangement
(pipeline.py + steps.py + models.py + utils.py)
        │
        ▼
Lecture-oriented structure tree
        │
        ▼
DB write-back / logical path materialization
(map_to_db_v2.py)
```

---

# Step 1 — Hierarchical BFS File Categorization

## Goal

Step 1 assigns every file in a raw course repository to one of three pedagogical categories:

- **study** — instructor-provided learning materials students read, watch, or review
- **practice** — assignments, labs, projects, starter materials, solution sets, and related student-facing task materials
- **support** — logistics, policies, staff info, textbooks, readings, and reference resources

Folder-level traversal also allows the system to infer:

- a **task name** (for example: `discussion`, `homework`, `lab`, `project`, `slides`)
- a **sequence name** (for example: `disc05`, `hw03`, `week02`, `partA`)
- a **category depth** showing how deep these semantics appear in the original path

These extra fields make Step 1 more than a flat classifier: it is the stage that produces the first **semantic filesystem layout** for the repository.

---

## Core design idea

Step 1 treats course organization as a **hierarchical classification problem** rather than classifying every file independently.

A filename like `sol.pdf` is ambiguous by itself. It could be:

- a homework solution,
- a discussion answer key, or
- an exam solution.

But inside `hw/hw03/` next to a problem set and starter files, its educational role becomes much clearer. The Step 1 pipeline therefore:

1. traverses the directory tree in **breadth-first order**,
2. tries to classify an entire folder first,
3. labels the whole subtree when the folder is coherent, and
4. only falls back to file-level classification when the folder is mixed or unclear.

This makes the system both **more accurate** and **more efficient**.

---

## Main scripts

### `bfs_v4.py`
The main Step 1 pipeline. It:

- scans the repository on disk,
- loads per-file descriptions from the metadata database,
- builds a folder tree,
- runs hierarchical BFS-based classification,
- infers task/sequence metadata,
- creates destination mappings,
- exports reports / JSON plans / tree JSON,
- and optionally copies the reorganized files to a destination folder.

### `classify_v4.py`
The LLM classification backend used by `bfs_v4.py`. It contains:

- shared data structures (`FileMeta`, `FolderNode`, `FileIndexEntry`, `FolderStats`, `ClassificationResult`)
- structured Pydantic output schemas for folder and file classification
- folder-level and file-level prompts
- task/sequence inference helpers
- timeouts and debug logging for LLM calls

Compared with earlier versions, v4 adds:

- **task / sequence extraction** as first-class outputs,
- **keyword-based task heuristics** for sparse or under-described files,
- stronger support for path-based semantic destination construction, and
- a 3-way file-level output system (`study`, `practice`, `support`) even when the model attempts `skip`.

---

## Step 1 inputs

Step 1 expects:

- a **source course repository** (directory on disk)
- a **SQLite metadata database** with a `file` table
- an **OpenAI API key** in the environment

The metadata DB is used to retrieve:

- `uuid`
- `file_name`
- `description`
- `relative_path`
- `extra_info`
- `file_hash`

These database descriptions provide the semantic signal that makes content-aware classification possible.

---

## Step 1 outputs

A Step 1 run can emit:

- `bfs_v4_report.md` — Markdown report
- `bfs_v4_plan.json` — mapping plan and stats
- `bfs_v4_tree.json` — tree-structured classification output
- `bfs_v4_llm_debug.json` — full prompt/response debug log
- reorganized files copied into a destination directory (if `--execute` is enabled)

Each file mapping contains at least:

- `source_rel`
- `dest_rel`
- `category`
- `task_name`
- `sequence_name`
- `category_depth`
- `reason`

---

## Step 1 path semantics

The destination path is built in a semantic format that aims to be more intuitive than the original repository.

A typical Step 1 destination looks like:

```text
<category>/<task>/<sequence>/...
```

Examples:

```text
practice/homework/hw03/problemset.pdf
study/slides/lecture05/lec05.pdf
support/resources/reference/robot_usage_guide.pdf
```

The pipeline also applies **container collapsing** to generic wrappers such as:

- `assets/`
- `resources/`
- `materials/`

when those folders add nesting but not pedagogical meaning.

---

## Step 1 classification behavior

At each folder node, the classifier uses three main sources of information:

1. **Structural signals**  
   file counts, extension distributions, subfolder names, recursive subtree size

2. **Semantic signals**  
   concatenated content descriptions from the metadata DB

3. **Ancestor context**  
   one-sentence descriptions from already-classified parent folders

The traversal then branches:

- **Confident, non-mixed folder**  
  The whole subtree inherits one category.

- **Mixed or skip folder**  
  The traversal continues downward and items are classified at finer granularity.

This design guarantees that **every file receives a final category**, while still exploiting folder-level regularity when it exists.

---

## Step 1 quick start

### Install dependencies

```bash
pip install openai pydantic python-dotenv
```

### Set your API key

```bash
export OPENAI_API_KEY="your_api_key_here"
```

### Run Step 1 classification only

```bash
python bfs_v4.py \
  --source "/path/to/course_repo" \
  --db "/path/to/course_metadata.db" \
  --model "gpt-5-mini-2025-08-07"
```

### Run Step 1 and copy reorganized files

```bash
python bfs_v4.py \
  --source "/path/to/course_repo" \
  --db "/path/to/course_metadata.db" \
  --model "gpt-5-mini-2025-08-07" \
  --execute \
  --dest "/path/to/course_repo_out"
```

### Useful outputs to inspect

- `bfs_v4_report.md`
- `bfs_v4_plan.json`
- `bfs_v4_tree.json`
- `bfs_v4_llm_debug.json`

---

# Step 2 — Lecture Labeling / Rearrangement Pipeline

Step 2 takes the output of Step 1 and performs a **deeper reorganization of study materials** around lecture/topic structure.

This README section is based on the current Step 2 implementation and its existing project documentation.

## Goal

Even after Step 1, the `study` folder may still be organized by format:

- lectures/
- discussions/
- videos/
- supplementary readings/

That is better than the raw repository, but still not how students typically review material.

Step 2 reorganizes the `study` subset around **lecture/topic units**, so that all relevant materials for the same lecture can be grouped together.

---

## Main Step 2 modules

### `models.py`
Holds the Pydantic schemas and shared pipeline dataclasses used across Step 2.

### `utils.py`
Provides:

- UTF-8-safe output helpers
- debug-log helpers
- path normalization helpers
- JSON loading and saving
- course-name derivation
- the `LLMGateway` wrapper around structured OpenAI completions

### `steps.py`
Contains the actual Step 2 domain logic:

0. pre-enrichment merge
1. enrichment
2. backbone identification
3. orphan collection
4. plan generation

### `pipeline.py`
The top-level orchestration layer and CLI entry point for Step 2.

### `file_rearrang.py`
Backwards-compatible re-export shim for older code using the previous interface.

---

## Step 2 inputs

Step 2 expects:

- a **tree JSON** from upstream processing
- a **metadata DB** with per-file descriptions and hashes
- optionally, a **final-paths JSON** from the earlier reorganization pass

These inputs are merged and enriched so that the Step 2 LLM sees not only filenames, but also human-readable descriptions of file contents.

---

## Step 2 pipeline stages

### 0. Pre-enrichment merge
If `--final-paths` is supplied, Step 2 first rebuilds or reorganizes the input tree so that files live at their upstream `final_path` locations before enrichment begins.

This supports:

- building from a routing file alone,
- reorganizing an existing first-pass tree, or
- combining both while preserving existing metadata such as `file_hash`.

A reorganized intermediate tree is saved as:

```text
outputs/<course>/v4_tree_reorganized.json
```

### 1. Enrichment
The pipeline walks the tree and attaches database descriptions to every file.

It then prunes the structure down to the `study` category (and optionally merges `practice` for multi-match mode).

The main output is:

```text
outputs/<course>/study_enriched.json
```

### 2. Backbone identification
The LLM is asked to identify the **chronological backbone** of the course — typically the folder that best represents lecture progression, such as:

- `study/slides`
- `study/lectures`

This yields a `backbone_result.json` file containing the chosen backbone path.

### 3. Orphan collection and matching
Everything outside the backbone is treated as an **orphan** that must be matched back to one or more lecture groups.

The orphan collector supports:

- **task-instance aggregation** (for example, collapsing an entire `Discussion_10/` folder into one unit)
- **leaf-folder aggregation**
- **batched LLM matching** against the lecture groups
- **hallucination filtering** and basename rescue for malformed paths returned by the LLM

This stage produces:

- `orphan_matches.json`
- `rearrangement_plan.json`

### 4. Tree materialization
The final Step 2 output is a lecture-oriented structure tree with file hashes included at the leaves.

It is written to:

```text
outputs/<course>/[multi/]rearrangement_structure_tree.json
```

This is the main artifact consumed downstream.

---

## Step 2 quick start

### Run the full Step 2 pipeline

```bash
python pipeline.py \
  --input bfs_v4_tree.json \
  --db "CS_61A_metadata.db" \
  --final-paths bfs_v4_final_paths.json \
  --course cs61a \
  --multi-match true
```

### Run without `final-paths`

```bash
python pipeline.py \
  --input bfs_v4_tree.json \
  --db "CS_61A_metadata.db" \
  --course cs61a
```

### Run a single Step 2 stage

```bash
python pipeline.py --step enrich   --input <tree>.json --db <db>.db --course <course>
python pipeline.py --step backbone --course <course>
python pipeline.py --step match    --course <course>
python pipeline.py --step tree     --course <course> --db <db>.db
```

---

## Step 2 outputs

Typical artifacts under `outputs/<course>/` or `outputs/<course>/multi/` include:

- `v4_tree_reorganized.json`
- `study_enriched.json`
- `backbone_result.json`
- `orphan_matches.json`
- `rearrangement_plan.json`
- `rearrangement_structure_tree.json`
- `debug/*.json`

These debug files are for observability and are **not** read back into the pipeline as inputs.

---

## Step 2 multi-match mode

The orphan matcher supports two modes:

- **single-match mode** — each orphan is assigned to one best lecture group
- **multi-match mode** — a file can be assigned to multiple lecture groups when it genuinely covers multiple topics

Multi-match is useful when a discussion worksheet, review file, or summary file spans several lecture topics and should remain visible in all relevant lecture folders.

---

## Step 2 hallucination mitigation

The matching stage applies several defenses against fabricated or malformed LLM outputs:

- exact-path matching
- whitespace-normalized matching
- basename rescue when the LLM drops or adds intermediate folders
- filtering of unknown or ambiguous returned paths

This ensures the final rearrangement plan only contains paths that correspond to real orphan items.

---

# DB Mapping / Materialization

## `map_to_db_v2.py`
This script is a downstream utility that writes reorganized logical paths back into course metadata databases.

It delegates to per-course implementations rather than forcing all courses through one rigid generic writer.

Current supported courses in the repo include at least:

- CS 61A
- EECS 106B

### What it does

- loads the correct course-specific mapping logic
- overrides the output table name
- supports dry-run mode
- creates a DB backup before real writes
- optionally cleans up nonessential tables after success
- optionally materializes the logical output tree (for supported courses)

### Example usage

```bash
python map_to_db_v2.py --course cs61a --dry-run
python map_to_db_v2.py --course eecs106b --dry-run
```

```bash
python map_to_db_v2.py --course cs61a --output-table file_new
python map_to_db_v2.py --course eecs106b --output-table file_new
```

---

# Evaluation summary

According to the capstone report, the system was evaluated against manually curated ground-truth structures. Reported results include:

- **Step 1 file categorization:** 99.4% accuracy on CS 61A
- **Step 2 lecture labeling:** 97.3% accuracy on CS 61A
- **End-to-end exact-match accuracy:** 81.7%
- improved exact-path accuracy over the raw baseline while preserving high category and filename recovery

The report also notes strong generalization behavior on additional courses such as **CS 106B** and **CS 288**.

---

# Typical workflow

A typical workflow for this repository is:

1. collect or crawl course materials into a raw repository
2. build / attach a metadata SQLite DB with file descriptions
3. run **Step 1** (`bfs_v4.py`) to categorize the repository into `study`, `practice`, and `support`
4. run **Step 2** (`pipeline.py`) to reorganize `study` around lecture/topic structure
5. optionally run **DB write-back** (`map_to_db_v2.py`) to persist logical paths
6. use the reorganized structure for browsing, evaluation, or TAI retrieval

---

# Design principles

This repository reflects several recurring design choices across both stages:

- **hierarchical over flat classification**
- **structured LLM output** via Pydantic schemas
- **non-destructive reorganization** (copy/symlink/logical path rather than destructive moves)
- **auditability** via JSON artifacts and debug logs
- **metadata-enriched reasoning** rather than filename-only heuristics
- **course-aware structure discovery** rather than pure embedding similarity

---

# Known limitations / future directions

Based on the current code and report, key limitations and future opportunities include:

- limited automated regression testing
- dependence on text descriptions when multimodal content may matter
- hierarchy mismatches that are semantically acceptable but exact-path different
- subjective differences in what counts as the “best” folder structure
- opportunities for instructor or GSI feedback loops
- opportunities for slide-visual, transcript, and notebook-structure signals

---

# Citation / project context

This repository implements the MEng capstone project described in the team report on **AI-assisted course repository reorganization for educational AI and student navigation**. The work is positioned as infrastructure for Berkeley TAI: organizing course materials so both humans and AI systems can find the right information more reliably.

---

# Contact / team

MEng Capstone Team 9

- Angelina Zhang
- Catherine Lee
- Derek Xu
- Yachen Wu
- Yu-Kai Hung

Subject area: **Educational AI and Learning Systems**

---

# License / usage note

No explicit repository license is documented in the materials referenced here. If you plan to reuse the code outside the capstone context, add a license file and verify any course-data handling constraints first.
