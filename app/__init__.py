"""Mr. Job Consultant: the local job search and application web app."""
import os

__version__ = "0.1.0"

# Never let huggingface_hub / gradio_client fall back to the Hugging Face login saved on this
# machine. Without this, any call made with token=None would silently run on the app owner's
# account (and ZeroGPU quota). The owner's login is only ever used explicitly, for the owner's
# own requests (see matching.resolve_token).
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
