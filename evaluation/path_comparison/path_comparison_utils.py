import re
from pathlib import Path

def normalize_directory_names(path):
    alnum_tokens_in_dirs = (re.findall("[a-zA-Z0-9]+", part) for part in path.parts)

    new__path_parts = []

    for path_tokens in alnum_tokens_in_dirs:
        normalized_name = "_".join(token.lower() for token in path_tokens)
        new__path_parts.append(normalized_name)

    return Path("/".join(new__path_parts))

def normalize_paths_cs61a(ground_truth, prediction):
    ground_truth_path_object = Path(ground_truth)

    if "CS 61A" in ground_truth_path_object.parts:
        ground_truth_path_object = ground_truth_path_object.relative_to("CS 61A")

    cleaned_ground_truth_path = ground_truth_path_object.parent

    normalized_ground_truth = normalize_directory_names(cleaned_ground_truth_path)
    
    cleaned_prediction_path = Path(prediction).parent

    if cleaned_prediction_path.parts[0] == ("original"):
        cleaned_prediction_path = cleaned_prediction_path.relative_to("original")

    if len(cleaned_prediction_path.parts) > 0 and cleaned_prediction_path.parts[0] == ("CS 61A"):
        cleaned_prediction_path = cleaned_prediction_path.relative_to("CS 61A")

    normalized_prediction = normalize_directory_names(cleaned_prediction_path)

    return normalized_ground_truth, normalized_prediction

def normalize_paths_eecs106b(ground_truth, prediction):
    cleaned_ground_truth_path = Path(ground_truth).parent
    normalized_ground_truth = normalize_directory_names(cleaned_ground_truth_path)
    
    cleaned_prediction_path = Path(prediction).parent

    if cleaned_prediction_path.parts[0] == "eecs106b":
        cleaned_prediction_path = cleaned_prediction_path.relative_to("eecs106b")

    normalized_prediction = normalize_directory_names(cleaned_prediction_path)

    return normalized_ground_truth, normalized_prediction