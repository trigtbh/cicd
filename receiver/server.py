from flask import Flask, request, jsonify
import os
import uuid
import json

app = Flask(__name__)

# Dictionary to track chunked uploads
chunk_tracker = {}

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
                    # Clean up tracking
                    del chunk_tracker[upload_key]
                    print(f"✓ Successfully combined {original_filename}")
                    return jsonify({
                        "message": f"All chunks received and combined successfully",
                        "id": upload_id,
                        "architecture": architecture,
                        "filename": original_filename
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
            
            try:
                file.save(f"./received_{file.filename}")
                print(f"✓ Successfully saved {file.filename}")
            except Exception as e:
                return jsonify({"error": f"Failed to save file: {str(e)}"}), 500
            
            return jsonify({
                "message": "File received successfully", 
                "id": upload_id,
                "architecture": architecture,
                "filename": file.filename
            }), 200
    
    except Exception as e:
        print(f"✗ Error in receive_data: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@app.route("/status", methods=["GET"])
def get_status():
    """Get the current status of chunked uploads"""
    status = {}
    for upload_key, info in chunk_tracker.items():
        status[upload_key] = {
            "received_chunks": len(info['received_chunks']),
            "total_chunks": info['total_chunks'],
            "progress": f"{len(info['received_chunks'])}/{info['total_chunks']}",
            "architecture": info['architecture'],
            "original_filename": info['original_filename']
        }
    return jsonify({"active_uploads": status}), 200


@app.route("/cleanup", methods=["POST"])
def cleanup_temp_files():
    """Clean up any leftover temporary files"""
    cleanup_count = 0
    for filename in os.listdir('.'):
        if filename.startswith('temp_') and '_chunk_' in filename:
            try:
                os.remove(filename)
                cleanup_count += 1
            except OSError:
                pass
    
    # Clear the tracker
    chunk_tracker.clear()
    
    return jsonify({
        "message": f"Cleaned up {cleanup_count} temporary files",
        "tracker_cleared": True
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)