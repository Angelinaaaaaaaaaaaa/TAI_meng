from contextlib import chdir
import argparse
from pathlib import Path
import json

from utils import normalize_db_path_eval, get_top_down_files
from build_data import get_ground_truth_hashes, construct_tree_from_json, create_folder_children_dict
from evaluation_core import evaluate_top_down, evaluate_bottom_up

def evaluate_tree(ground_truth_tree, prediction_tree, method, limit=3):
    if method == "top-down":
        ground_truth_layers = get_top_down_files(ground_truth_tree, limit)
        prediction_layers = get_top_down_files(prediction_tree, limit)

        evaluation_report = evaluate_top_down(ground_truth_layers, prediction_layers, limit=limit)
    elif method == "bottom-up":
        evaluation_report = evaluate_bottom_up(ground_truth_tree, prediction_tree, limit)

    return evaluation_report

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--database_path", "-d", type=str, default="databases/CS 61A_metadata_reorganization.db", help="The path to the SQLite database containing file hashes and original paths")
    parser.add_argument("--ground_truth_path", "-g", type=str, default="test_arrangements/61A_reorganized_groundtruth", help="The path to the ground truth arrangement")
    parser.add_argument("--prediction_path", "-p", type=str, default="test_arrangements/bfs_v4_final_paths.json", help="The path to the prediction arrangement paths in JSON format")
    parser.add_argument("--method", "-m", type=str, choices=["top-down", "bottom-up"], default="top-down", help="The evaluation method to use")
    parser.add_argument("--limit", "-l", type=int, default=3, help="The number of path components to consider for evaluation")

    args = parser.parse_args()

    unnormalized_ground_truth_hashes = get_ground_truth_hashes(args.database_path)

    prediction_tree = construct_tree_from_json(unnormalized_ground_truth_hashes, args.prediction_path)

    ground_truth_hashes = {normalize_db_path_eval(path): file_hash for path, file_hash in unnormalized_ground_truth_hashes.items()}

    ground_truth_tree = {}

    with chdir(args.ground_truth_path):
        create_folder_children_dict(Path("."), ground_truth_tree, ground_truth_hashes)

    evaluation_report = evaluate_tree(ground_truth_tree, prediction_tree, method=args.method, limit=args.limit)

    with open(f"evaluation_reports/{Path(args.ground_truth_path).stem}_{Path(args.prediction_path).stem}_{args.method}_limit_{args.limit}.json", "w") as f:
        json.dump(evaluation_report, f, indent=2)

if __name__ == "__main__":
    main()