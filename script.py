import os
import re
for root, dirs, files in os.walk('.'):
    if root.startswith('./__pycache__') or '.git' in root:
        continue
    for f in files:
        if not f.endswith('.py'):
            continue
        path = os.path.join(root, f)
        with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
            if 'merge_segments' in fh.read():
                print(path)
