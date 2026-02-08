# gpu_check.py
import torch

print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("cuda version:", torch.version.cuda)
    print("device:", torch.cuda.get_device_name(0))
    print("capability:", torch.cuda.get_device_capability(0))
    print("memory GB:", round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2))
