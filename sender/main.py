import argparse
import os
import config
import requests
import sys
import time

def split_file(file_path, chunk_size_mb=5):
    """Split a file into chunks of specified size in MB"""
    chunk_size_bytes = chunk_size_mb * 1024 * 1024  # Convert MB to bytes
    chunk_files = []
    
    with open(file_path, 'rb') as f:
        chunk_num = 0
        while True:
            chunk_data = f.read(chunk_size_bytes)
            if not chunk_data:
                break
            
            chunk_filename = f"{file_path}.part{chunk_num:03d}"
            with open(chunk_filename, 'wb') as chunk_file:
                chunk_file.write(chunk_data)
            
            chunk_files.append(chunk_filename)
            print(f"Created chunk: {chunk_filename} ({len(chunk_data)} bytes)")
            chunk_num += 1
    
    return chunk_files

def send_request_with_retry(url, files=None, data=None, max_retries=3, timeout=30):
    """Send HTTP request with retry logic"""
    for attempt in range(max_retries):
        try:
            response = requests.post(url, files=files, data=data, timeout=timeout)
            response.raise_for_status()  # Raise an exception for bad status codes
            return response
        except requests.exceptions.RequestException as e:
            print(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {2 ** attempt} seconds...")
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                print("All retry attempts failed.")
                raise

def validate_server_connection():
    """Test connection to server"""
    try:
        response = requests.get(config.server, timeout=10)
        response.raise_for_status()
        print(f"✓ Server connection successful: {response.text.strip()}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"✗ Server connection failed: {e}")
        print(f"Please check if the server is running at {config.server}")
        return False

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
    
    # Validate inputs
    if not config.server or config.server.strip() == "":
        print("✗ Error: Server URL not configured in config.py")
        sys.exit(1)
    
    if not os.path.exists(target_folder):
        print(f"✗ Error: Target folder '{target_folder}' does not exist")
        sys.exit(1)
    
    print(f"Packaging folder: {target_folder}")
    print(f"Target architecture: {architecture}")
    print(f"Server URL: {config.server}")
    
    # Test server connection
    if not validate_server_connection():
        sys.exit(1)
    
    # Create archive with architecture in filename
    archive_name = f"data_{architecture}.tar.gz"
    print(f"Creating archive: {archive_name}")
    
    # Use tar command (works on Windows with Git Bash or WSL)
    result = os.system(f"tar -czf {archive_name} {target_folder}")
    if result != 0:
        print("✗ Error: Failed to create tar archive. Make sure tar is available on your system.")
        sys.exit(1)
    
    if not os.path.exists(archive_name):
        print(f"✗ Error: Archive {archive_name} was not created")
        sys.exit(1)
    
    print(f"✓ Created archive: {archive_name}")
    
    # Check file size and split if necessary
    file_size = os.path.getsize(archive_name)
    print(f"Archive size: {file_size / (1024*1024):.2f} MB")
    
    server_url = config.server.strip("/") + "/data"
    
    try:
        if file_size > 5 * 1024 * 1024:  # 5MB in bytes
            print("Archive is larger than 5MB, splitting into chunks...")
            chunk_files = split_file(archive_name)
            
            # Send each chunk to the server
            for i, chunk_file in enumerate(chunk_files):
                print(f"Sending chunk {i+1}/{len(chunk_files)}...")
                with open(chunk_file, "rb") as f:
                    chunk_data = {
                        "architecture": architecture,
                        "chunk_index": str(i),
                        "total_chunks": str(len(chunk_files)),
                        "original_filename": archive_name
                    }
                    response = send_request_with_retry(server_url, 
                                                     files={"file": f}, 
                                                     data=chunk_data)
                    print(f"✓ Chunk {i+1}/{len(chunk_files)} sent successfully")
                    print(f"  Server response: {response.text}")
            
            # Clean up chunk files
            for chunk_file in chunk_files:
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)
                    print(f"Cleaned up chunk: {chunk_file}")
        else:
            print("Archive is under 5MB, sending as single file...")
            # Send the archive to the server
            with open(archive_name, "rb") as f:
                response = send_request_with_retry(server_url, 
                                                 files={"file": f}, 
                                                 data={"architecture": architecture})
                print(f"✓ File sent successfully")
                print(f"  Server response: {response.text}")
        
        print("✓ Upload completed successfully!")
        
    except requests.exceptions.RequestException as e:
        print(f"✗ Error sending file to server: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        sys.exit(1)
    finally:
        # Clean up original archive
        if os.path.exists(archive_name):
            os.remove(archive_name)
            print(f"Cleaned up original archive: {archive_name}")

if __name__ == "__main__":
    main()