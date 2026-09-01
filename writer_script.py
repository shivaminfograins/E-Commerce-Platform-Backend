import sys
import base64
import os

if len(sys.argv) < 3:
    print("Usage: writer_script.py <target_path> <base64_content>")
    sys.exit(1)

target_path = sys.argv[1]
b64_content = sys.argv[2]

# Ensure directory exists
os.makedirs(os.path.dirname(target_path), exist_ok=True)

# Decode and write
with open(target_path, "wb") as f:
    f.write(base64.b64decode(b64_content))

print(f"Successfully wrote {target_path}")