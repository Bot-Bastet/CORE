import shutil
import os
from pathlib import Path
from huggingface_hub import scan_cache_dir

def clean_old_cache():
    print("Scanning Hugging Face Cache...")
    try:
        hf_cache_info = scan_cache_dir()
        
        found = False
        for repo in hf_cache_info.repos:
            if "NemoMix" in repo.repo_id:
                print(f"Found model in cache: {repo.repo_id}")
                print(f"Path: {repo.repo_path}")
                
                confirm = input("Delete this model from C: drive? (y/n): ")
                if confirm.lower() == 'y':
                    print("Deleting...")
                    try:
                        shutil.rmtree(repo.repo_path)
                        print("Successfully deleted model folder.")
                        # Blobs might remain if they are global, but this clears the main reference
                    except Exception as e:
                        print(f"Error deleting: {e}")
                found = True
        
        if not found:
            print("No 'NemoMix' model found in standard cache.")
            
    except Exception as e:
        print(f"Error scanning cache: {e}")
        # Fallback manual check
        manual_path = Path.home() / ".cache/huggingface/hub/models--MarinaraSpaghetti--NemoMix-Unleashed-12B"
        if manual_path.exists():
            print(f"Found manual path: {manual_path}")
            confirm = input("Delete this folder? (y/n): ")
            if confirm.lower() == 'y':
                shutil.rmtree(manual_path)
                print("Deleted.")

if __name__ == "__main__":
    clean_old_cache()
