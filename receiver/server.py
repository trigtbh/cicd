from flask import Flask, request, jsonify
import os
import uuid
import json
import subprocess

app = Flask(__name__)

# Dictionary to track chunked uploads
chunk_tracker = {}

def extract_tar_file(tar_file_path, extract_id):
    """Extract tar file to a unique directory"""
    try:
        # Create extraction directory with unique ID
        extract_dir = f"./{extract_id}"
        
        # Create directory if it doesn't exist
        os.makedirs(extract_dir, exist_ok=True)
        
        # Extract tar file with strip-components=1
        cmd = ["tar", "-xzf", tar_file_path, "-C", extract_dir, "--strip-components=1"]
        
        print(f"Extracting {tar_file_path} to {extract_dir}...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✓ Successfully extracted to {extract_dir}")
            # Remove the tar file after successful extraction
            os.remove(tar_file_path)
            print(f"✓ Cleaned up tar file: {tar_file_path}")
            return True, extract_dir
        else:
            print(f"✗ Extraction failed: {result.stderr}")
            return False, f"Extraction failed: {result.stderr}"
    
    except Exception as e:
        print(f"✗ Error during extraction: {str(e)}")
        return False, f"Error during extraction: {str(e)}"

def build_docker_image(extract_dir, architecture, upload_id):
    """Build Docker image for the specified architecture"""
    try:
        # Check if Dockerfile exists
        dockerfile_path = os.path.join(extract_dir, "Dockerfile")
        if not os.path.exists(dockerfile_path):
            return False, "No Dockerfile found in extracted directory"
        
        # Generate image name with architecture and upload_id
        image_name = f"cicd-build-{architecture}-{upload_id[:8]}"
        
        # Map architecture names to Docker platform format
        platform_map = {
            "x86": "linux/386",
            "x64": "linux/amd64", 
            "arm": "linux/arm/v7",
            "arm64": "linux/arm64"
        }
        
        platform = platform_map.get(architecture, "linux/amd64")
        
        # Build Docker command
        cmd = [
            "docker", "build",
            "--platform", platform,
            "-t", image_name,
            extract_dir
        ]
        
        print(f"Building Docker image {image_name} for platform {platform}...")
        print(f"Command: {' '.join(cmd)}")
        
        # Run docker build
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"✓ Successfully built Docker image: {image_name}")
            return True, {
                "image_name": image_name,
                "platform": platform,
                "build_output": result.stdout
            }
        else:
            print(f"✗ Docker build failed: {result.stderr}")
            return False, f"Docker build failed: {result.stderr}"
    
    except Exception as e:
        print(f"✗ Error during Docker build: {str(e)}")
        return False, f"Error during Docker build: {str(e)}"

def combine_chunks(original_filename, total_chunks, upload_id):
    """Combine all chunks into the original file"""
    combined_file_path = f"./received_{original_filename}"
    
    try:
        # Verify all chunks exist before combining
        missing_chunks = []
        for i in range(total_chunks):
            chunk_path = f"./temp_{upload_id}_chunk_{i}"
            if not os.path.exists(chunk_path):
                missing_chunks.append(i)
        
        if missing_chunks:
            return False, f"Missing chunks: {missing_chunks}"
        
        # Combine chunks
        with open(combined_file_path, 'wb') as combined_file:
            for i in range(total_chunks):
                chunk_path = f"./temp_{upload_id}_chunk_{i}"
                try:
                    with open(chunk_path, 'rb') as chunk_file:
                        combined_file.write(chunk_file.read())
                    os.remove(chunk_path)  # Clean up chunk file
                    print(f"Combined and removed chunk {i}")
                except Exception as e:
                    return False, f"Error processing chunk {i}: {str(e)}"
        
        print(f"✓ Successfully created combined file: {combined_file_path}")
        return True, "File combined successfully"
    
    except Exception as e:
        return False, f"Error combining chunks: {str(e)}"

@app.route("/")
def index():
    return "Receiver Server is running."


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "message": "Receiver Server is running and ready to accept uploads",
        "active_uploads": len(chunk_tracker)
    }), 200


@app.route("/data", methods=["POST"])
def receive_data():
    """Receive .tar.gz file or chunks"""
    try:
        # Validate file in request
        if 'file' not in request.files:
            return jsonify({"error": "No file part in the request"}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        
        # Get additional data from the request
        architecture = request.form.get('architecture', 'unknown')
        chunk_index = request.form.get('chunk_index')
        total_chunks = request.form.get('total_chunks')
        original_filename = request.form.get('original_filename')
        
        upload_id = str(uuid.uuid4())
        
        # Handle chunked upload
        if chunk_index is not None and total_chunks is not None and original_filename is not None:
            try:
                chunk_index = int(chunk_index)
                total_chunks = int(total_chunks)
            except ValueError:
                return jsonify({"error": "Invalid chunk_index or total_chunks format"}), 400
            
            # Validate chunk parameters
            if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
                return jsonify({"error": "Invalid chunk parameters"}), 400
            
            if not original_filename.endswith(".tar.gz"):
                return jsonify({"error": "Invalid original file type, only .tar.gz allowed"}), 400
            
            print(f"Receiving chunk {chunk_index + 1}/{total_chunks} for {original_filename}")
            
            # Create a unique identifier for this chunked upload
            upload_key = f"{original_filename}_{architecture}"
            
            # Initialize tracking for this upload if not exists
            if upload_key not in chunk_tracker:
                chunk_tracker[upload_key] = {
                    'received_chunks': set(),
                    'total_chunks': total_chunks,
                    'original_filename': original_filename,
                    'architecture': architecture,
                    'upload_id': upload_id
                }
            else:
                upload_id = chunk_tracker[upload_key]['upload_id']
                # Validate consistency
                if chunk_tracker[upload_key]['total_chunks'] != total_chunks:
                    return jsonify({"error": "Inconsistent total_chunks for this upload"}), 400
            
            # Save the chunk
            chunk_path = f"./temp_{upload_id}_chunk_{chunk_index}"
            try:
                file.save(chunk_path)
            except Exception as e:
                return jsonify({"error": f"Failed to save chunk: {str(e)}"}), 500
            
            # Mark this chunk as received
            chunk_tracker[upload_key]['received_chunks'].add(chunk_index)
            
            print(f"Saved chunk {chunk_index} to {chunk_path}")
            print(f"Received {len(chunk_tracker[upload_key]['received_chunks'])}/{total_chunks} chunks")
            
            # Check if all chunks are received
            if len(chunk_tracker[upload_key]['received_chunks']) == total_chunks:
                print("All chunks received, combining...")
                success, message = combine_chunks(original_filename, total_chunks, upload_id)
                
                if success:
                    # Extract the combined tar file
                    tar_file_path = f"./received_{original_filename}"
                    extract_success, extract_result = extract_tar_file(tar_file_path, upload_id)
                    
                    # Clean up tracking
                    del chunk_tracker[upload_key]
                    
                    if extract_success:
                        # Build Docker image for the specified architecture
                        build_success, build_result = build_docker_image(extract_result, architecture, upload_id)
                        
                        if build_success:
                            print(f"✓ Successfully combined, extracted, and built Docker image for {original_filename}")
                            return jsonify({
                                "message": f"All chunks received, combined, extracted, and Docker image built successfully",
                                "id": upload_id,
                                "architecture": architecture,
                                "filename": original_filename,
                                "extracted_to": extract_result,
                                "docker_image": build_result["image_name"],
                                "platform": build_result["platform"]
                            }), 200
                        else:
                            print(f"✓ Successfully combined and extracted, but Docker build failed: {build_result}")
                            return jsonify({
                                "message": f"All chunks received, combined, and extracted successfully, but Docker build failed",
                                "id": upload_id,
                                "architecture": architecture,
                                "filename": original_filename,
                                "extracted_to": extract_result,
                                "docker_build_error": build_result
                            }), 200
                    else:
                        print(f"✗ Combined successfully but extraction failed: {extract_result}")
                        return jsonify({
                            "message": f"All chunks received and combined successfully, but extraction failed",
                            "id": upload_id,
                            "architecture": architecture,
                            "filename": original_filename,
                            "extraction_error": extract_result
                        }), 200
                else:
                    return jsonify({"error": f"Failed to combine chunks: {message}"}), 500
            else:
                return jsonify({
                    "message": f"Chunk {chunk_index + 1}/{total_chunks} received successfully",
                    "chunks_received": len(chunk_tracker[upload_key]['received_chunks']),
                    "chunks_total": total_chunks
                }), 200
        
        # Handle single file upload (non-chunked)
        else:
            if not file.filename.endswith(".tar.gz"):
                return jsonify({"error": "Invalid file type, only .tar.gz allowed"}), 400
            
            print(f"Receiving single file: {file.filename}")
            
            tar_file_path = f"./received_{file.filename}"
            try:
                file.save(tar_file_path)
                print(f"✓ Successfully saved {file.filename}")
                
                # Extract the tar file
                extract_success, extract_result = extract_tar_file(tar_file_path, upload_id)
                
                if extract_success:
                    # Build Docker image for the specified architecture
                    build_success, build_result = build_docker_image(extract_result, architecture, upload_id)
                    
                    if build_success:
                        print(f"✓ Successfully received, extracted, and built Docker image for {file.filename}")
                        return jsonify({
                            "message": "File received, extracted, and Docker image built successfully", 
                            "id": upload_id,
                            "architecture": architecture,
                            "filename": file.filename,
                            "extracted_to": extract_result,
                            "docker_image": build_result["image_name"],
                            "platform": build_result["platform"]
                        }), 200
                    else:
                        print(f"✓ Successfully received and extracted, but Docker build failed: {build_result}")
                        return jsonify({
                            "message": "File received and extracted successfully, but Docker build failed",
                            "id": upload_id,
                            "architecture": architecture,
                            "filename": file.filename,
                            "extracted_to": extract_result,
                            "docker_build_error": build_result
                        }), 200
                else:
                    print(f"✗ Received successfully but extraction failed: {extract_result}")
                    return jsonify({
                        "message": "File received successfully, but extraction failed",
                        "id": upload_id,
                        "architecture": architecture,
                        "filename": file.filename,
                        "extraction_error": extract_result
                    }), 200
                    
            except Exception as e:
                return jsonify({"error": f"Failed to save file: {str(e)}"}), 500
    
    except Exception as e:
        print(f"✗ Error in receive_data: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500


def get_built_docker_images():
    """Get list of Docker images built by this server"""
    try:
        # List Docker images with our naming pattern
        cmd = ["docker", "images", "--format", "json", "--filter", "reference=cicd-build-*"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            images = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        image_info = json.loads(line)
                        # Parse architecture and upload_id from image name
                        tag_parts = image_info['Repository'].split('-')
                        if len(tag_parts) >= 4:  # cicd-build-{arch}-{upload_id}
                            architecture = tag_parts[2]
                            upload_id_short = tag_parts[3]
                            images.append({
                                "name": image_info['Repository'],
                                "tag": image_info['Tag'],
                                "architecture": architecture,
                                "upload_id_short": upload_id_short,
                                "size": image_info['Size'],
                                "created": image_info['CreatedSince']
                            })
                    except json.JSONDecodeError:
                        pass
            return images
        else:
            return []
    except Exception as e:
        print(f"Error listing Docker images: {e}")
        return []

@app.route("/status", methods=["GET"])
def get_status():
    """Get the current status of chunked uploads, extracted directories, and built Docker images"""
    status = {}
    for upload_key, info in chunk_tracker.items():
        status[upload_key] = {
            "received_chunks": len(info['received_chunks']),
            "total_chunks": info['total_chunks'],
            "progress": f"{len(info['received_chunks'])}/{info['total_chunks']}",
            "architecture": info['architecture'],
            "original_filename": info['original_filename']
        }
    
    # List extracted directories
    extracted_dirs = []
    for item in os.listdir('.'):
        if os.path.isdir(item) and not item.startswith('.') and not item == '__pycache__':
            # Check if it looks like a UUID (extracted directory)
            try:
                uuid.UUID(item)
                extracted_dirs.append(item)
            except ValueError:
                pass
    
    # Get built Docker images
    docker_images = get_built_docker_images()
    
    return jsonify({
        "active_uploads": status,
        "extracted_directories": extracted_dirs,
        "docker_images": docker_images
    }), 200


@app.route("/cleanup", methods=["POST"])
def cleanup_temp_files():
    """Clean up any leftover temporary files and extracted directories"""
    cleanup_count = 0
    
    # Clean up chunk files
    for filename in os.listdir('.'):
        if filename.startswith('temp_') and '_chunk_' in filename:
            try:
                os.remove(filename)
                cleanup_count += 1
            except OSError:
                pass
        # Clean up leftover received tar files
        elif filename.startswith('received_') and filename.endswith('.tar.gz'):
            try:
                os.remove(filename)
                cleanup_count += 1
            except OSError:
                pass
    
    # Clean up extracted directories (UUID directories)
    extracted_cleanup_count = 0
    for item in os.listdir('.'):
        if os.path.isdir(item) and not item.startswith('.') and not item == '__pycache__':
            # Check if it looks like a UUID (extracted directory)
            try:
                uuid.UUID(item)
                import shutil
                shutil.rmtree(item)
                extracted_cleanup_count += 1
                print(f"Removed extracted directory: {item}")
            except ValueError:
                pass
            except OSError as e:
                print(f"Failed to remove directory {item}: {e}")
    
    # Clean up Docker images built by this server
    docker_cleanup_count = 0
    try:
        cmd = ["docker", "images", "-q", "--filter", "reference=cicd-build-*"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            image_ids = result.stdout.strip().split('\n')
            for image_id in image_ids:
                if image_id:  # Skip empty lines
                    try:
                        rm_cmd = ["docker", "rmi", "-f", image_id]
                        rm_result = subprocess.run(rm_cmd, capture_output=True, text=True)
                        if rm_result.returncode == 0:
                            docker_cleanup_count += 1
                            print(f"Removed Docker image: {image_id}")
                        else:
                            print(f"Failed to remove Docker image {image_id}: {rm_result.stderr}")
                    except Exception as e:
                        print(f"Error removing Docker image {image_id}: {e}")
    except Exception as e:
        print(f"Error during Docker cleanup: {e}")
    
    # Clear the tracker
    chunk_tracker.clear()
    
    return jsonify({
        "message": f"Cleaned up {cleanup_count} temporary files, {extracted_cleanup_count} extracted directories, and {docker_cleanup_count} Docker images",
        "temp_files_cleaned": cleanup_count,
        "directories_cleaned": extracted_cleanup_count,
        "docker_images_cleaned": docker_cleanup_count,
        "tracker_cleared": True
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)