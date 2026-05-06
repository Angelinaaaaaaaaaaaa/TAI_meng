import re
import argparse
import sqlite3
from pathlib import Path

def normalize_directory_names(path):
    alnum_tokens_in_dirs = (re.findall("[a-zA-Z0-9]+", part) for part in path.parts)

    new__path_parts = []

    for path_tokens in alnum_tokens_in_dirs:
        normalized_name = "_".join(token.lower() for token in path_tokens)
        new__path_parts.append(normalized_name)

    return Path("/".join(new__path_parts))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database_path", "-d", type=str, default="databases/EECS_106B_metadata.db", help="The path to the SQLite database containing ground truth and predicted paths")

    args = parser.parse_args()

    conn = sqlite3.connect(args.database_path)

    cursor = conn.execute("SELECT relative_path, file_path FROM file_new_hybrid")

    total_paths = 0
    total_exact_match_correct = 0
    total_top_down_mismatch = 0
    total_bottom_up_mismatch = 0

    while (curr_row := cursor.fetchone()):
        ground_truth, prediction = curr_row
        
        cleaned_ground_truth_path = Path(ground_truth).parent
        normalized_ground_truth = normalize_directory_names(cleaned_ground_truth_path)
        
        cleaned_prediction_path = Path(prediction).parent

        if cleaned_prediction_path.parts[0] == "eecs106b":
            cleaned_prediction_path = cleaned_prediction_path.relative_to("eecs106b")

        normalized_prediction = normalize_directory_names(cleaned_prediction_path)

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

    total_exact_match_incorrect = total_paths - total_exact_match_correct

    print(
        f"Exact match proportion: {total_exact_match_correct / total_paths}\n"
        f"Average top-down first mismatched directory: {total_top_down_mismatch / total_exact_match_incorrect}\n"
        f"Average bottom-up first mismatched directory: {total_bottom_up_mismatch / total_exact_match_incorrect}"
    )

if __name__ == "__main__":
    main()