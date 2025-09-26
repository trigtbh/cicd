from flask import Flask, request, jsonify, send_file
import os
import uuid
import json
import subprocess
import time

app = Flask(__name__)

# Dictionary to track chunked uploads
chunk_tracker = {}

# Dictionary to track exported image chunks available for download
image_chunks = {}

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
            "docker", "buildx", "build",
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

def export_and_split_docker_image(image_name, upload_id, chunk_size_mb=5):
    """Export Docker image and split it into chunks"""
    try:
        # Export the Docker image to a tar file
        export_filename = f"docker_image_{upload_id[:8]}.tar"
        export_path = f"./{export_filename}"
        
        print(f"Exporting Docker image {image_name} to {export_path}...")
        
        # Export Docker image
        export_cmd = ["docker", "save", "-o", export_path, image_name]
        result = subprocess.run(export_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return False, f"Failed to export Docker image: {result.stderr}"
        
        print(f"✓ Successfully exported Docker image to {export_path}")
        
        # Split the exported tar file into chunks
        chunk_size_bytes = int(chunk_size_mb * 1024 * 1024)
        chunk_files = []
        
        with open(export_path, 'rb') as f:
            chunk_num = 0
            while True:
                chunk_data = f.read(chunk_size_bytes)
                if not chunk_data:
                    break
                
                chunk_filename = f"image_chunk_{upload_id[:8]}_{chunk_num:03d}.tar"
                chunk_path = f"./{chunk_filename}"
                
                with open(chunk_path, 'wb') as chunk_file:
                    chunk_file.write(chunk_data)
                
                chunk_files.append(chunk_filename)
                print(f"Created image chunk: {chunk_filename} ({len(chunk_data)} bytes)")
                chunk_num += 1
        
        # Remove the original export file
        os.remove(export_path)
        print(f"✓ Cleaned up export file: {export_path}")
        
        return True, {
            "chunk_files": chunk_files,
            "total_chunks": len(chunk_files),
            "original_size": os.path.getsize(chunk_files[0]) * (len(chunk_files) - 1) + os.path.getsize(chunk_files[-1]) if chunk_files else 0
        }
        
    except Exception as e:
        print(f"✗ Error during Docker image export/split: {str(e)}")
        return False, f"Error during Docker image export/split: {str(e)}"

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
                            # Export and split the Docker image
                            export_success, export_result = export_and_split_docker_image(
                                build_result["image_name"], upload_id
                            )
                            
                            if export_success:
                                # Store chunk information for download
                                image_chunks[upload_id] = {
                                    "image_name": build_result["image_name"],
                                    "architecture": architecture,
                                    "chunk_files": export_result["chunk_files"],
                                    "total_chunks": export_result["total_chunks"],
                                    "original_size": export_result["original_size"],
                                    "created_at": time.time()
                                }
                                
                                print(f"✓ Successfully combined, extracted, built, and exported Docker image for {original_filename}")
                                return jsonify({
                                    "message": f"All chunks received, combined, extracted, Docker image built and exported successfully",
                                    "id": upload_id,
                                    "architecture": architecture,
                                    "filename": original_filename,
                                    "extracted_to": extract_result,
                                    "docker_image": build_result["image_name"],
                                    "platform": build_result["platform"],
                                    "image_chunks_available": export_result["total_chunks"],
                                    "image_size": export_result["original_size"]
                                }), 200
                            else:
                                print(f"✓ Successfully built Docker image but export failed: {export_result}")
                                return jsonify({
                                    "message": f"All chunks received, combined, extracted, and Docker image built successfully, but export failed",
                                    "id": upload_id,
                                    "architecture": architecture,
                                    "filename": original_filename,
                                    "extracted_to": extract_result,
                                    "docker_image": build_result["image_name"],
                                    "platform": build_result["platform"],
                                    "export_error": export_result
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
                        # Export and split the Docker image
                        export_success, export_result = export_and_split_docker_image(
                            build_result["image_name"], upload_id
                        )
                        
                        if export_success:
                            # Store chunk information for download
                            image_chunks[upload_id] = {
                                "image_name": build_result["image_name"],
                                "architecture": architecture,
                                "chunk_files": export_result["chunk_files"],
                                "total_chunks": export_result["total_chunks"],
                                "original_size": export_result["original_size"],
                                "created_at": time.time()
                            }
                            
                            print(f"✓ Successfully received, extracted, built, and exported Docker image for {file.filename}")
                            return jsonify({
                                "message": "File received, extracted, Docker image built and exported successfully", 
                                "id": upload_id,
                                "architecture": architecture,
                                "filename": file.filename,
                                "extracted_to": extract_result,
                                "docker_image": build_result["image_name"],
                                "platform": build_result["platform"],
                                "image_chunks_available": export_result["total_chunks"],
                                "image_size": export_result["original_size"]
                            }), 200
                        else:
                            print(f"✓ Successfully built Docker image but export failed: {export_result}")
                            return jsonify({
                                "message": "File received, extracted, and Docker image built successfully, but export failed",
                                "id": upload_id,
                                "architecture": architecture,
                                "filename": file.filename,
                                "extracted_to": extract_result,
                                "docker_image": build_result["image_name"],
                                "platform": build_result["platform"],
                                "export_error": export_result
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


@app.route("/image/<upload_id>/info", methods=["GET"])
def get_image_info(upload_id):
    """Get information about available image chunks for download"""
    if upload_id not in image_chunks:
        return jsonify({"error": "Image chunks not found for this upload ID"}), 404
    
    chunk_info = image_chunks[upload_id]
    return jsonify({
        "upload_id": upload_id,
        "image_name": chunk_info["image_name"],
        "architecture": chunk_info["architecture"],
        "total_chunks": chunk_info["total_chunks"],
        "original_size": chunk_info["original_size"],
        "created_at": chunk_info["created_at"],
        "chunk_files": chunk_info["chunk_files"]
    }), 200


@app.route("/image/<upload_id>/chunk/<int:chunk_index>", methods=["GET"])
def download_image_chunk(upload_id, chunk_index):
    """Download a specific chunk of the Docker image"""
    if upload_id not in image_chunks:
        return jsonify({"error": "Image chunks not found for this upload ID"}), 404
    
    chunk_info = image_chunks[upload_id]
    
    if chunk_index < 0 or chunk_index >= chunk_info["total_chunks"]:
        return jsonify({"error": f"Invalid chunk index. Valid range: 0-{chunk_info['total_chunks']-1}"}), 400
    
    chunk_filename = chunk_info["chunk_files"][chunk_index]
    chunk_path = f"./{chunk_filename}"
    
    if not os.path.exists(chunk_path):
        return jsonify({"error": f"Chunk file {chunk_filename} not found"}), 404
    
    try:
        print(f"Sending chunk {chunk_index + 1}/{chunk_info['total_chunks']}: {chunk_filename}")
        return send_file(chunk_path, as_attachment=True, download_name=chunk_filename)
    except Exception as e:
        return jsonify({"error": f"Failed to send chunk: {str(e)}"}), 500


@app.route("/image/<upload_id>/complete", methods=["POST"])
def mark_image_download_complete(upload_id):
    """Mark image download as complete and clean up chunks"""
    if upload_id not in image_chunks:
        return jsonify({"error": "Image chunks not found for this upload ID"}), 404
    
    chunk_info = image_chunks[upload_id]
    cleanup_count = 0
    
    # Clean up chunk files
    for chunk_filename in chunk_info["chunk_files"]:
        chunk_path = f"./{chunk_filename}"
        try:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)
                cleanup_count += 1
                print(f"Cleaned up image chunk: {chunk_filename}")
        except OSError as e:
            print(f"Failed to remove chunk {chunk_filename}: {e}")
    
    # Remove from tracking
    del image_chunks[upload_id]
    
    return jsonify({
        "message": f"Image download marked complete, cleaned up {cleanup_count} chunk files",
        "upload_id": upload_id,
        "chunks_cleaned": cleanup_count
    }), 200


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
    """Get the current status of chunked uploads, extracted directories, built Docker images, and available image chunks"""
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
    
    # Get available image chunks for download
    available_images = {}
    for upload_id, chunk_info in image_chunks.items():
        available_images[upload_id] = {
            "image_name": chunk_info["image_name"],
            "architecture": chunk_info["architecture"],
            "total_chunks": chunk_info["total_chunks"],
            "original_size": chunk_info["original_size"],
            "created_at": chunk_info["created_at"]
        }
    
    return jsonify({
        "active_uploads": status,
        "extracted_directories": extracted_dirs,
        "docker_images": docker_images,
        "available_image_chunks": available_images
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
        # Clean up image chunk files
        elif filename.startswith('image_chunk_') and filename.endswith('.tar'):
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
    
    # Clear the trackers
    chunk_tracker.clear()
    image_chunks.clear()
    
    return jsonify({
        "message": f"Cleaned up {cleanup_count} temporary files, {extracted_cleanup_count} extracted directories, and {docker_cleanup_count} Docker images",
        "temp_files_cleaned": cleanup_count,
        "directories_cleaned": extracted_cleanup_count,
        "docker_images_cleaned": docker_cleanup_count,
        "tracker_cleared": True
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)