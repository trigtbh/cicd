import argparse
import os
import config
import requests
import sys
import time

# Global verbosity level
verbosity = 0

def vprint(message, level=1):
    """Print message only if verbosity level is high enough"""
    global verbosity
    if verbosity >= level:
        print(message)

def split_file(file_path, chunk_size_mb=5):
    """Split a file into chunks of specified size in MB"""
    chunk_size_bytes = int(chunk_size_mb * 1024 * 1024)  # Convert MB to bytes

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
            vprint(f"Created chunk: {chunk_filename} ({len(chunk_data)} bytes)", 2)
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
            vprint(f"Attempt {attempt + 1} failed: {e}", 1)
            if attempt < max_retries - 1:
                vprint(f"Retrying in {2 ** attempt} seconds...", 1)
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                print("All retry attempts failed.")
                raise

def validate_server_connection():
    """Test connection to server"""
    try:
        response = requests.get(config.server, timeout=10)
        response.raise_for_status()
        vprint(f"Server connection successful: {response.text.strip()}", 1)
        return True
    except requests.exceptions.RequestException as e:
        print(f"✗ Server connection failed: {e}")
        vprint(f"Please check if the server is running at {config.server}", 1)
        return False

def main():
    global verbosity
    
    parser = argparse.ArgumentParser(description="Package and send a folder for CI/CD deployment")
    parser.add_argument("folder",
                        help="Target folder as a relative path to package")
    parser.add_argument("-a", "--architecture",
                        choices=["x86", "x64", "arm", "arm64"],
                        default="x64",
                        help="Target architecture to build for (default: x64)")
    parser.add_argument("-v", "--verbose", 
                        action="count", 
                        default=0,
                        help="Increase verbosity (-v for basic info, -vv for detailed info)")
    parser.add_argument("-q", "--quiet", 
                        action="store_true",
                        help="Suppress all output except errors")

    args = parser.parse_args()

    target_folder = args.folder
    architecture = args.architecture
    verbosity = args.verbose
    
    # Handle quiet mode
    if args.quiet:
        verbosity = -1

    # Validate inputs
    if not config.server or config.server.strip() == "":
        print("✗ Error: Server URL not configured in config.py")
        sys.exit(1)

    if not os.path.exists(target_folder):
        print(f"✗ Error: Target folder '{target_folder}' does not exist")
        sys.exit(1)

    vprint(f"Packaging folder: {target_folder}", 1)
    vprint(f"Target architecture: {architecture}", 1)
    vprint(f"Server URL: {config.server}", 2)

    # Test server connection
    print("Connecting...")
    if not validate_server_connection():
        sys.exit(1)

    # Create archive with architecture in filename
    archive_name = f"data_{architecture}.tar.gz"
    print("Taring...")
    vprint(f"Creating archive: {archive_name}", 1)

    # Use tar command (works on Windows with Git Bash or WSL)
    result = os.system(f"tar -czf {archive_name} {target_folder}")
    if result != 0:
        print("✗ Error: Failed to create tar archive. Make sure tar is available on your system.")
        sys.exit(1)

    if not os.path.exists(archive_name):
        print(f"✗ Error: Archive {archive_name} was not created")
        sys.exit(1)

    vprint(f"✓ Created archive: {archive_name}", 1)

    # Check file size and split if necessary
    file_size = os.path.getsize(archive_name)
    vprint(f"Archive size: {file_size / (1024*1024):.2f} MB", 1)

    server_url = config.server.strip("/") + "/data"

    try:
        if file_size > 5 * 1024 * 1024:  # 5MB in bytes
            print("Splitting...")
            vprint("Archive is larger than 5MB, splitting into chunks...", 1)
            chunk_files = split_file(archive_name, chunk_size_mb=0.75)

            # Send each chunk to the server
            print("Sending...")
            for i, chunk_file in enumerate(chunk_files):
                vprint(f"Sending chunk {i+1}/{len(chunk_files)}...", 1)
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
                    vprint(f"✓ Chunk {i+1}/{len(chunk_files)} sent successfully", 1)
                    vprint(f"  Server response: {response.text}", 2)

            # Clean up chunk files
            for chunk_file in chunk_files:
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)
                    vprint(f"Cleaned up chunk: {chunk_file}", 2)
        else:
            print("Sending...")
            vprint("Archive is under 5MB, sending as single file...", 1)
            # Send the archive to the server
            with open(archive_name, "rb") as f:
                response = send_request_with_retry(server_url,
                                                 files={"file": f},
                                                 data={"architecture": architecture})
                vprint(f"✓ File sent successfully", 1)
                vprint(f"  Server response: {response.text}", 2)

        print("Complete!")
        vprint("✓ Upload completed successfully!", 1)

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
            vprint(f"Cleaned up original archive: {archive_name}", 2)

if __name__ == "__main__":
    main()