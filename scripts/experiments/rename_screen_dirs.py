import argparse
import os
import sys

sys.path.append("../..")
from src.utils.basic.io import get_file_list


if __name__ == "__main__":
    sys.path.append("../..")
    
    arg_parser = argparse.ArgumentParser(description="Script runner.")

    arg_parser.add_argument(
        "--root_dir",
        metavar="ROOT_DIR",
        help="Root directory of the screen results",
        required=True,
    )
    args = arg_parser.parse_args()

    root_dir = args.root_dir
    for config_file in get_file_list(
        root_dir=root_dir,
        absolute_path=True,
        file_ending=False,
        file_type_filter=".yml",
    ):
        dir, file = os.path.split(config_file)
        # target = file.split("_")[-1]
        target = file
        os.rename(dir, os.path.join(root_dir, target))
    sys.exit(0)
