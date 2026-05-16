import argparse
import sqlite3
from pathlib import Path
import json

from path_comparison_utils import normalize_paths_cs61a, normalize_paths_eecs106b

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database_path", "-d", type=str, default="databases/CS 61A_metadata_comparison.db", help="The path to the SQLite database containing ground truth and predicted paths")

    args = parser.parse_args()

    conn = sqlite3.connect(args.database_path)

    cursor = conn.execute(f"SELECT relative_path, file_path FROM {'file_new' if 'CS 61A_metadata_comparison.db' in args.database_path else 'file_new_hybrid'}")

    total_paths = 0
    total_exact_match_correct = 0
    total_top_down_mismatch = 0
    total_bottom_up_mismatch = 0

    mismatches = []

    while (curr_row := cursor.fetchone()):
        ground_truth, prediction = curr_row

        if "CS 61A_metadata_comparison.db" in args.database_path:
            path_normalizer = normalize_paths_cs61a
        elif "EECS_106B_metadata.db" in args.database_path:
            path_normalizer = normalize_paths_eecs106b
        
        normalized_ground_truth, normalized_prediction = path_normalizer(ground_truth, prediction)

        total_paths += 1

        if normalized_ground_truth == normalized_prediction:
            total_exact_match_correct += 1
        else:
            first_top_down_mismatch = 1

            for ground_truth_part, prediction_part in zip(normalized_ground_truth.parts, normalized_prediction.parts):
                if ground_truth_part == prediction_part:
                    first_top_down_mismatch += 1
                else:
                    break

            total_top_down_mismatch += first_top_down_mismatch

            first_bottom_up_mismatch = 1

            for ground_truth_part, prediction_part in zip(reversed(normalized_ground_truth.parts), reversed(normalized_prediction.parts)):
                if ground_truth_part == prediction_part:
                    first_bottom_up_mismatch += 1
                else:
                    break

            total_bottom_up_mismatch += first_bottom_up_mismatch

            mismatches.append({
                "ground_truth": str(ground_truth),
                "prediction": str(prediction),
                "first_top_down_mismatch": first_top_down_mismatch,
                "first_bottom_up_mismatch": first_bottom_up_mismatch
            })

    total_exact_match_incorrect = total_paths - total_exact_match_correct

    evaluation_report = {
        "summary": {
            "exact_match_proportion": total_exact_match_correct / total_paths,
            "average_top_down_first_mismatch": total_top_down_mismatch / total_exact_match_incorrect if total_exact_match_incorrect > 0 else 0,
            "average_bottom_up_first_mismatch": total_bottom_up_mismatch / total_exact_match_incorrect if total_exact_match_incorrect > 0 else 0
        },
        "mismatches": mismatches
    }

    with open(f"evaluation_reports/evaluation_report_{Path(args.database_path).stem}.json", "w") as f:
        json.dump(evaluation_report, f, indent=2)

if __name__ == "__main__":
    main()