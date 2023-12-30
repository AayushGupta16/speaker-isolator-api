from flask import Flask, request, abort
from flask_cors import CORS
from flask_restful import Resource, Api
from main import process_video
from urllib.parse import urlparse
import base64
import os
import re
import io

app = Flask(__name__)
CORS(app)
api = Api(app)

class VideoProcessing(Resource):
    def post(self):
        # Validate incoming data
        if not request.json or 'youtube_url' not in request.json or 'api_key' not in request.json:
            abort(400, description="Invalid request. Please provide 'youtube_url' and 'api_key'.")

        youtube_url = request.json['youtube_url']
        api_key = request.json['api_key'].strip()

        # Validate API key length
        if len(api_key) != 32:
            print(api_key)
            abort(400, description="Invalid API key. It should be 32 characters long.")

        # Validate YouTube URL
        parsed_url = urlparse(youtube_url)
        if not re.match(r'^(www\.)?youtube\.com', parsed_url.netloc):
            abort(400, description="Invalid YouTube URL.")

        try:
            # Process the YouTube video and separate the audio by speaker
            speakers_audio = process_video(api_key, youtube_url, 'youtube_audio', use_saved_data=False,
                                           transcript_file_path="transcript.json")

            audio_files = []
            for speaker_audio_tuple in speakers_audio:
                file_like_object = io.BytesIO()
                speaker_audio_tuple[1].export(file_like_object, format="mp3")
                audio_base64 = base64.b64encode(file_like_object.getvalue()).decode('utf-8')

                filename = speaker_audio_tuple[0]
                split_name = filename.split('_')
                speaker_name = split_name[1]
                part_number = split_name[3]

                display_name = f"Speaker {speaker_name}"
                if len(speakers_audio) > 1:
                    display_name += f" Part {part_number}"

                audio_files.append({'name': display_name, 'data': audio_base64})

            return {'audio_files': audio_files}, 200

        except Exception as e:
            abort(500, description=str(e))

api.add_resource(VideoProcessing, '/process_video')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
