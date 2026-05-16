from contextlib import chdir
import argparse
from pathlib import Path
import json

from reorganization_utils import normalize_db_path_eval, get_top_down_files
from build_data import get_ground_truth_hashes, construct_tree_from_json, create_folder_children_dict
from evaluation_core import evaluate_top_down, evaluate_bottom_up, get_comparison

def evaluate_tree(ground_truth_tree, prediction_tree, method, limit=3):
    if method == "top_down":
        ground_truth_layers = get_top_down_files(ground_truth_tree, limit)
        prediction_layers = get_top_down_files(prediction_tree, limit)

        evaluation_report = evaluate_top_down(ground_truth_layers, prediction_layers, limit=limit)
    elif method == "bottom_up":
        evaluation_report = evaluate_bottom_up(ground_truth_tree, prediction_tree, limit)

    return evaluation_report

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--database_path", "-d", type=str, default="databases/CS 61A_metadata_reorganization.db", help="The path to the SQLite database containing file hashes and original paths")
    parser.add_argument("--ground_truth_path", "-g", type=str, default="test_arrangements/61A_reorganized_groundtruth", help="The path to the ground truth arrangement")
    parser.add_argument("--prediction_path", "-p", type=str, default="test_arrangements/bfs_v4_final_paths.json", help="The path to the prediction arrangement paths in JSON format")
    parser.add_argument("--limit", "-l", type=int, default=3, help="The number of path components to consider for evaluation")

    args = parser.parse_args()

    unnormalized_ground_truth_hashes = get_ground_truth_hashes(args.database_path)

    ground_truth_hashes = {normalize_db_path_eval(path): file_hash for path, file_hash in unnormalized_ground_truth_hashes.items()}

    with chdir(args.ground_truth_path):
        ground_truth_tree = create_folder_children_dict(ground_truth_hashes)

    prediction_tree = construct_tree_from_json(unnormalized_ground_truth_hashes, args.prediction_path)

    comparison = get_comparison(ground_truth_tree, prediction_tree)

    report_path_template = f"evaluation_reports/{Path(args.ground_truth_path).stem}_{Path(args.prediction_path).stem}_{{}}_limit_{args.limit}.json"

    for method in ("top_down", "bottom_up"):
        evaluation_report = evaluate_tree(ground_truth_tree, prediction_tree, method=method, limit=args.limit)

        with open(report_path_template.format(method), "w") as f:
            json.dump(evaluation_report, f, indent=2)

    with open(report_path_template.format("paths_correspondences"), "w") as f:
        json.dump(comparison, f, indent=2)

if __name__ == "__main__":
    main()