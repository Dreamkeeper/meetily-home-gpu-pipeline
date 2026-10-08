import os, sys, pathlib, warnings
import torch
os.add_dll_directory(str(pathlib.Path(torch.__file__).parent / "lib"))
print("python", sys.version.split()[0], "torch", torch.__version__, "cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
free, total = torch.cuda.mem_get_info(); print(f"vram free {free/2**30:.1f} / {total/2**30:.1f} GB")
import ctranslate2; print("ctranslate2", ctranslate2.__version__, "cuda devices", ctranslate2.get_cuda_device_count())
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    import pyannote.audio; print("pyannote", pyannote.audio.__version__)
    for x in w[:5]: print("WARN:", str(x.message)[:200])
import transformers; print("transformers", transformers.__version__)
