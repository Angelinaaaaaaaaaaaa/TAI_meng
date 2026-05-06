from pathlib import Path

from file_folder_objects import Folder

def evaluate_top_down(ground_truth_folder_data, prediction_folder_data, limit):
    def _get_folder_score(folder, limit):
        evaluate_deeper = len(folder.path.parts) != limit
        
        ground_truth_folder_content = ground_truth_folder_data[folder]["subfolders"]
        
        true_labels = len(ground_truth_folder_content) if evaluate_deeper else 0
        
        children = []

        folder_in_prediction = folder in prediction_folder_data

        if evaluate_deeper and folder_in_prediction:
            prediction_folder_content = prediction_folder_data[folder]["subfolders"]

            false_positives = sorted(str(path) for path in prediction_folder_content - ground_truth_folder_content)
            false_negatives = sorted(str(path) for path in ground_truth_folder_content - prediction_folder_content)

            true_positives = len(ground_truth_folder_content & prediction_folder_content)
            positives = len(prediction_folder_content)

            for subfolder in ground_truth_folder_data[folder]["subfolders"]:
                tp, t_labels, pos, folder_info = _get_folder_score(subfolder, limit)

                true_positives += tp
                true_labels += t_labels
                positives += pos
                children.append(folder_info)
        else:
            true_positives = 0
            positives = 0

        children.sort(key=lambda x: x["folder"])

        precision = true_positives / positives if positives > 0 else 0
        recall = true_positives / true_labels if true_labels > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if {precision, recall} != {0} else 0
        
        if true_labels == 0 and positives == 0:
            folder_info = {"folder": str(folder), "children": "Children and evaluation metrics are excluded as this subfolder contains no children to this depth"}
        else:
            folder_info = {"folder": str(folder), "precision": precision, "recall": recall, "f1": f1}
        
            if f1 == 1:
                folder_info["children"] = "Children are excluded as this subfolder is correctly arranged"
            else:
                folder_info["children"] = children

                folder_info["false_positives"] = false_positives if folder_in_prediction else []
                folder_info["false_negatives"] = false_negatives if folder_in_prediction else sorted(str(path) for path in ground_truth_folder_content)

        return true_positives, true_labels, positives, folder_info
    
    *_, eval_info = _get_folder_score(Folder(Path(".")), limit)
    return eval_info

def evaluate_bottom_up(ground_truth_dict, prediction_dict, limit):
    prediction_files = {file:Folder(file.path) for children in prediction_dict.values() for file in children["files"]}

    ground_truth_file_to_prediction_path = {file: prediction_files[file] for children in ground_truth_dict.values() for file in children["files"]}

    def _get_folder_score(folder, limit):
        children = ground_truth_dict[folder]

        total = 0
        correct = 0

        incorrect_paths = []
        
        for file in children["files"]:
            prediction_path = ground_truth_file_to_prediction_path[file]

            if folder.path == Path("."):
                total += 1

                curr_file_correct = len(prediction_path.path.parts) == 1

                correct += int(curr_file_correct)

                if not curr_file_correct:
                    incorrect_paths.append(str(prediction_path))
            else:
                curr_total, curr_correct = folder.compare_bottom_up(prediction_path, limit)

                total += curr_total

                correct += curr_correct

                if curr_total != curr_correct:
                    incorrect_paths.append(str(prediction_path))

        children_acc_info = []

        for subfolder in children["subfolders"]:
            total_cont, correct_cont, subfolder_info = _get_folder_score(subfolder, limit)
            
            total += total_cont
            correct += correct_cont
            children_acc_info.append(subfolder_info)

        children_acc_info.sort(key=lambda x: x["folder"])

        folder_info = {"folder": str(folder)}

        if total == 0:
            folder_info["children"] = "Children and accuracy are excluded as this subfolder contains no files"
        else:
            accuracy = correct / total
            folder_info["accuracy"] = accuracy

            if total == correct:
                folder_info["children"] = "Children are excluded as this subfolder is correctly arranged"
            else:
                folder_info["children"] = children_acc_info

                if len(incorrect_paths) != 0:
                    incorrect_paths.sort()
                    folder_info["incorrect_paths"] = incorrect_paths

        return total, correct, folder_info

    *_, eval_info = _get_folder_score(Folder(Path('.')), limit)
    return eval_info