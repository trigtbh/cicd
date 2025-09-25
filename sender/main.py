import sys

# second argument is target folder as a relative path

if len(sys.argv) < 2:
    print("Please provide a target folder as a relative path.")
    sys.exit(1)
else:
    target_folder = sys.argv[1]

import os

os.system(f"tar -czf data.tar.gz {target_folder}")