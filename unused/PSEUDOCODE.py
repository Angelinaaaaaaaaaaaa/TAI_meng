# BFS Reorg PSEUDOCODE

function BFS_REORGANIZE(course_root, db_path, classifier, threshold):
    # 0) Load DB file summaries (all exist already)
    db = DB(db_path)
    # Map: original_path -> {uuid, file_summary, file_name}
    file_index = db.load_all_files_index()

    # 1) Build filesystem tree (FolderNode / FileMeta)
    files_on_disk = scan_directory(course_root, exclude_dirs=DEFAULT_EXCLUDE_DIRS)
    root = build_tree(course_root, files_on_disk)

    # 2) BFS queue init: top-level folders + root files
    Q = Queue()
    for folder in root.children:
        Q.push(folder)
    for f in root.files:
        Q.push(f)

    folder_decisions = Map()   # folder_path -> FolderDecision (category/confidence/reason/folder_description)
    mappings = List()          # per-file move plan (source_rel -> dest_rel)

    while Q not empty:
        item = Q.pop_left()

        if item is FolderNode:
            # ---- Gather folder context (needed for classification) ----
            # All file summaries in THIS folder subtree (use DB prefix query OR tree traversal)
            folder_files = collect_all_files(item)                 # list[FileMeta] (subtree)
            concat_desc  = concat([ file_index[f.source_path].file_summary for f in folder_files if exists ],
                                  sep="\n\n", cap_chars=MAX_CHARS)

            folder_stats = compute_folder_stats(item)              # counts/ext/subfolders/etc (cheap)
            # NOTE: classification requires concatenated file descriptions (file_summary)

            # ---- Folder classification (REASON -> FOLDER_SUMMARY -> DECIDE) ----
            # Step A: reason about the folder (no category yet)
            reason_text = classifier.reason_folder(
                folder_path=item.path,
                folder_name=item.name,
                folder_stats=folder_stats,
                concat_file_summaries=concat_desc
            )

            # Step B: generate folder_description AFTER reasoning (always, even if skip)
            folder_description = classifier.summarize_folder(
                folder_path=item.path,
                folder_stats=folder_stats,
                reason_text=reason_text
            )

            # Step C: decide category/confidence AFTER folder_description exists
            decision = classifier.decide_folder(
                folder_path=item.path,
                folder_stats=folder_stats,
                reason_text=reason_text,
                folder_description=folder_description
            )
            # decision = {category, confidence, is_mixed, final_reason}

            # ---- Persist folder_description into DB for ALL files in this folder subtree ----
            # Write SAME folder_description into the NEW column for every file in this folder
            uuids = [ file_index[f.source_path].uuid for f in folder_files if exists ]
            db.update_folder_description_bulk(uuids, folder_description)

            # ---- Record folder-level decision (always) ----
            folder_decisions[item.path] = {
                "folder_path": item.path,
                "category": decision.category,          # study/practice/support/skip
                "confidence": decision.confidence,
                "reason": decision.final_reason,        # includes reason_text if desired
                "folder_description": folder_description,
                "is_mixed": decision.is_mixed
            }

            # ---- BFS control: skip also descends (does NOT terminate the subtree) ----
            should_descend =
                (decision.confidence < threshold) OR
                (decision.is_mixed == True) OR
                (decision.category == "skip")

            if NOT should_descend:
                # Confident folder-level routing: all files inherit the folder category
                for f in folder_files:
                    dest_rel = build_dest_rel(decision.category, top_folder=item.name, tail=tail_from(item.path, f.source_path))
                    mappings.append({ "source_rel": f.source_path, "dest_rel": dest_rel, "category": decision.category,
                                      "reason": "Inherited from folder: " + decision.final_reason })
                continue

            # Descend: enqueue subfolders + files for finer-grained classification
            for child_folder in item.children:
                Q.push(child_folder)
            for f in item.files:
                Q.push(f)
            continue

        else if item is FileMeta:
            # ---- File-level classification ----
            file_summary = file_index[item.source_path].file_summary if exists else ""
            fdec = classifier.classify_file(
                file_path=item.source_path,
                file_name=item.file_name,
                file_summary=file_summary
            )

            if fdec.category == "skip":
                continue

            dest_rel = build_dest_rel(fdec.category, top_folder=top_level_folder(item.source_path),
                                      tail=tail_from(top_level_folder(item.source_path), item.source_path))
            mappings.append({ "source_rel": item.source_path, "dest_rel": dest_rel,
                              "category": fdec.category, "reason": fdec.reason })
            continue

    return { "folder_decisions": folder_decisions, "mappings": mappings }

# Classifier PSEUDOCODE

class Classifier:
    init(model):
        self.model = model

    # ---- Folder: Step A (reason first, no category yet) ----
    function reason_folder(folder_path, folder_name, folder_stats, concat_file_summaries) -> string:
        prompt = build_reason_prompt(folder_path, folder_name, folder_stats, concat_file_summaries)
        # LLM returns a detailed reasoning text only
        reason_text = LLM(self.model).generate_text(prompt)
        return reason_text

    # ---- Folder: Step B (generate folder_description after reasoning) ----
    function summarize_folder(folder_path, folder_stats, reason_text) -> string:
        prompt = build_folder_summary_prompt(folder_path, folder_stats, reason_text)
        # LLM returns a short "folder_description" used for DB + UI/README
        folder_description = LLM(self.model).generate_text(prompt)
        return folder_description

    # ---- Folder: Step C (decide category/confidence after summary exists) ----
    function decide_folder(folder_path, folder_stats, reason_text, folder_description) -> Decision:
        prompt = build_decide_prompt(folder_path, folder_stats, reason_text, folder_description)
        # LLM returns JSON: {category, confidence, is_mixed, final_reason}
        decision = LLM(self.model).parse_json(prompt)
        return decision

    # ---- File classification (single step) ----
    function classify_file(file_path, file_name, file_summary) -> FileDecision:
        prompt = build_file_classify_prompt(file_path, file_name, file_summary)
        # LLM returns JSON: {category, confidence, reason}
        fdec = LLM(self.model).parse_json(prompt)
        return fdec

