import sqlite3
from pathlib import Path
import json
from collections import defaultdict

from utils import create_path_repr, normalize_ground_truth_path, normalize_db_path_find_hashes
from file_folder_objects import File, Folder

def get_ground_truth_hashes(db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT file_hash, original_path FROM file")

        hashes = {}
        
        while True:
            row = cursor.fetchone()

            if row is None:
                break

            file_hash, original_path = row

            path_without_root = Path(original_path).relative_to("CS 61A_unstructured")

            hashes[path_without_root] = file_hash

    return hashes

def construct_tree_from_json(ground_truth_hashes, final_paths_file):
    with open(final_paths_file, encoding="utf-8") as f:
        final_paths = json.load(f)

    ground_truth_hashes_normalized_path = {normalize_db_path_find_hashes(path): file_hash for path, file_hash in ground_truth_hashes.items()}

    prediction_tree = defaultdict(lambda: {"files": set(), "subfolders": set()})

    for file_info in final_paths["all_final_paths"]:
        curr_hash = ground_truth_hashes_normalized_path[normalize_db_path_find_hashes(Path(file_info["source"]))]

        curr_predicted_path = Path(file_info["final_path"])

        immediate_parent = Folder(Path("."))

        for part in curr_predicted_path.parts[:-1]:
            curr_folder_path = Folder(immediate_parent.path / part)

            prediction_tree[immediate_parent]["subfolders"].add(curr_folder_path)

            immediate_parent = curr_folder_path

        prediction_tree[immediate_parent]["files"].add(File(curr_predicted_path, curr_hash))

    return prediction_tree

def create_folder_children_dict(path, output, hashes):
    path_repr = Folder(create_path_repr(path))

    output[path_repr] = {"files": set(), "subfolders": set()}

    for item in path.iterdir():
        if item.is_file():
            output[path_repr]["files"].add(File(item, hashes[normalize_ground_truth_path(item)]))
        elif item.is_dir():
            output[path_repr]["subfolders"].add(Folder(create_path_repr(item)))

            create_folder_children_dict(item, output, hashes)