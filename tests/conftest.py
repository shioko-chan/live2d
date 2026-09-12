import os

# All tests are CPU/protocol-only. Do not import torch, use CUDA, or launch model services.
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
