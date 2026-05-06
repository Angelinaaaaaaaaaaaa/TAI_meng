import re
from pathlib import Path

from utils import create_path_repr

class File:
    def __init__(self, path, file_hash):
        self.path = create_path_repr(path)
        self.hash = hash(file_hash)

    def __hash__(self):
        return self.hash

    def __eq__(self, other):
        return self.hash == other.hash

    def __str__(self):
        return str(self.path)
    
    def __repr__(self):
        return f"File({self.path})"

class Folder:
    def __init__(self, path):
        self.path = path

        normalized_path_parts = []

        for part in path.parts:
            part_lower = part.lower()
            part_split = part_lower.split("_")

            if len(part_split) == 2 and part_split[0].isalpha() and part_split[1].isdigit():
                part_lower = "".join(part_split)

            part_numbers_normalized = "".join(str(int(part)) if part.isdigit() else part for part in re.split(r"(\d+)", part_lower))

            part_normalized = part_numbers_normalized.replace("homework", "hw").replace("discussion", "disc").replace("project", "proj")

            normalized_path_parts.append(part_normalized)

        self.normalized_path = Path(*normalized_path_parts)

    def compare_bottom_up(self, other, limit):
        ground_truth_comparison_len = min(limit, len(self.normalized_path.parts))
        prediction_comparison_len = min(ground_truth_comparison_len, len(other.normalized_path.parts) - 1)

        comparison_start_ind = -prediction_comparison_len - 1

        ground_truth_to_compare = self.normalized_path.parts[-prediction_comparison_len:]
        predictions_to_compare = other.normalized_path.parts[comparison_start_ind:-1]

        num_parts_correct = sum(g == p for g, p in zip(ground_truth_to_compare, predictions_to_compare))

        return ground_truth_comparison_len, num_parts_correct

    def __hash__(self):
        return hash(self.normalized_path)

    def __eq__(self, other):
        return self.normalized_path == other.normalized_path

    def __str__(self):
        return str(self.path)
    
    def __repr__(self):
        return f"Folder({self.path})"