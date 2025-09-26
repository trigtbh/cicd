import argparse
import os

def main():
    parser = argparse.ArgumentParser(description="Package and send a folder for CI/CD deployment")
    parser.add_argument("folder", 
                        help="Target folder as a relative path to package")
    parser.add_argument("-a", "--architecture", 
                        choices=["x86", "x64", "arm", "arm64"], 
                        default="x64",
                        help="Target architecture to build for (default: x64)")
    
    args = parser.parse_args()
    
    target_folder = args.folder
    architecture = args.architecture
    
    print(f"Packaging folder: {target_folder}")
    print(f"Target architecture: {architecture}")
    
    # Create archive with architecture in filename
    archive_name = f"data_{architecture}.tar.gz"
    os.system(f"tar -czf {archive_name} {target_folder}")
    
    print(f"Created archive: {archive_name}")

if __name__ == "__main__":
    main()