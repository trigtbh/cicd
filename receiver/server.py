from flask import Flask, request, jsonify


app = Flask(__name__)

import uuid


@app.route("/")
def index():
    return "Receiver Server is running."


@app.route("/data", methods=["POST"])
def receive_data():
    # receive .tar.gz file
    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if not file.filename.endswith(".tar.gz"):
        return jsonify({"error": "Invalid file type, only .tar.gz allowed"}), 400
    
    
    id_ = str(uuid.uuid4())


    
    file.save(f"./received_{file.filename}")
    return jsonify({"message": "File received successfully", "id": id_}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)