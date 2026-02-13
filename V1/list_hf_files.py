from huggingface_hub import list_repo_files

repo_id = "macadeliccc/dolphin-2.9.3-mistral-7B-32K-GGUF"
try:
    files = list_repo_files(repo_id)
    print(f"Files in {repo_id}:")
    for f in files:
        if f.endswith(".gguf"):
            print(f)
except Exception as e:
    print(f"Error accessing repo: {e}")
