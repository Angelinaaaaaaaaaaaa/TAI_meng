from pathlib import Path
import re

def create_path_repr(path):
    if len(path.parts) > 0 and path.parts[0] == "C:\\":
        path = Path(*path.parts[1:])

    return path

def normalize_ground_truth_path(path):
    return Path(*(part.replace("∩╝Ü", "：") for part in path.parts[1:] if not(part[:-2] in {"lab", "slides", "hw", "disc"} and part[-2:].isdigit())))

def normalize_db_path_find_hashes(path):
    return Path(*("_".join(re.findall(r"\w+", part.lower())) for part in path.parts))

def normalize_db_path_eval(path):
    if path.parts[0] == "assets":
        return Path(*("study-guide" if part == "pdfs" else part for part in path.parts[1:]))
    else:
        return Path(*(part for part in path.parts if not (part[:-2] in {"hw", "disc"} and part[-2:].isdigit())))
    
def get_top_down_files(children_dict, limit):
    return {path: {"files": content["files"], "subfolders": set() if len(path.path.parts) == limit else content["subfolders"]} for path, content in children_dict.items() if len(path.path.parts) <= limit}