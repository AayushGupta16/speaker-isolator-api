import os
import io
import re
import logging
import zipfile
from flask import Flask, request, abort, send_file
from flask_cors import CORS
from urllib.parse import urlparse
from pytube import YouTube
from pydub import AudioSegment
import requests
import time

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
CORS(app)

# Constants
API_KEY = os.environ.get("ASSEMBLY_AI_API_KEY")  # Get the AssemblyAI API key from the environment variable
ASSEMBLY_AI_UPLOAD_ENDPOINT = "https://api.assemblyai.com/v2/upload"
ASSEMBLY_AI_TRANSCRIPT_ENDPOINT = "https://api.assemblyai.com/v2/transcript"
PAUSE_DURATION_MS = 500  # Pause duration between speaker segments in milliseconds

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def download_youtube_video(url):
    """
    Download the audio from a YouTube video.
    :param url: The URL of the YouTube video.
    :return: The audio data as an io.BytesIO object.
    """
    try:
        audio_data = io.BytesIO()
        yt = YouTube(url)
        stream = yt.streams.filter(only_audio=True).first()  # Get the first audio stream
        stream.stream_to_buffer(audio_data)  # Download the audio stream and write it to the audio_data buffer
        audio_data.seek(0)  # Reset the buffer position to the beginning
        audio = AudioSegment.from_file(audio_data, format="mp4")
        mp3_data = io.BytesIO()
        audio.export(mp3_data, format="mp3")  # Convert the audio to MP3 format
        mp3_data.seek(0)  # Reset the buffer position to the beginning
        return mp3_data
    except Exception as e:
        logger.error(f"Error downloading YouTube video: {str(e)}")
        raise

def upload_audio(audio_bytes, api_key):
    """
    Upload the audio data to AssemblyAI.
    :param audio_bytes: The audio data as bytes.
    :param api_key: The AssemblyAI API key.
    :return: The upload URL.
    """
    headers = {
        "authorization": api_key,
        "content-type": "application/octet-stream",
    }
    response = requests.post(ASSEMBLY_AI_UPLOAD_ENDPOINT, headers=headers, data=audio_bytes)
    response.raise_for_status()  # Raise an exception for non-2xx status codes
    return response.json()["upload_url"]

def get_transcript(upload_url, api_key):
    """
    Get the transcript from AssemblyAI.
    :param upload_url: The upload URL returned by the upload_audio function.
    :param api_key: The AssemblyAI API key.
    :return: The transcript data.
    """
    try:
        headers = {"authorization": api_key, "content-type": "application/json"}
        json_data = {
            "audio_url": upload_url,
            "speaker_labels": True,  # Request speaker labels in the transcript
        }

        response = requests.post(ASSEMBLY_AI_TRANSCRIPT_ENDPOINT, json=json_data, headers=headers)
        response.raise_for_status()
        transcript_id = response.json()["id"]

        while True:
            response = requests.get(f"{ASSEMBLY_AI_TRANSCRIPT_ENDPOINT}/{transcript_id}", headers=headers)
            response.raise_for_status()
            transcript_data = response.json()

            if transcript_data["status"] == "completed":
                return transcript_data
            elif transcript_data["status"] == "error":
                raise Exception(f"Transcription failed: {transcript_data['error']}")
            else:
                logger.info("Transcription processing...")
                time.sleep(5)  # Wait for 5 seconds before polling again

    except Exception as e:
        logger.error(f"Error getting transcript: {str(e)}")
        raise

def create_speaker_segments(transcript, audio_bytes):
    """
    Create speaker segments from the transcript and audio data.
    :param transcript: The transcript data returned by the get_transcript function.
    :param audio_bytes: The audio data as bytes.
    :return: A list of tuples containing the speaker segment filename and audio segment.
    """
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")  # Load the audio data

        speaker_segments = {}
        speaker_individual_segments = {}

        for utterance in transcript["utterances"]:  # Process each utterance in the transcript
            speaker = utterance["speaker"]
            start = utterance["start"]
            end = utterance["end"]

            segment = audio[start:end]  # Extract the audio segment for the current utterance

            if speaker not in speaker_segments:
                speaker_segments[speaker] = segment
            else:
                pause = AudioSegment.silent(duration=PAUSE_DURATION_MS)
                speaker_segments[speaker] += pause + segment  # Concatenate the segments with a pause

            if speaker not in speaker_individual_segments:
                speaker_individual_segments[speaker] = [segment]
            else:
                speaker_individual_segments[speaker].append(segment)

        output_files = []
        for speaker, audio_segment in speaker_segments.items():  # Export the audio files for each speaker
            output_files.append((f"speaker_{speaker}.mp3", audio_segment))

        for speaker, segments in speaker_individual_segments.items():  # Export the individual audio files for each speaker
            for i, segment in enumerate(segments):
                output_files.append((f"speaker_{speaker}_segment_{i+1}.mp3", segment))

        return output_files
    except Exception as e:
        logger.error(f"Error creating speaker segments: {str(e)}")
        raise

@app.route('/process_video', methods=['POST'])
def process_video_endpoint():
    """
    Process a YouTube video and return the isolated speaker audio files.
    Expected JSON payload: {"youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID"}
    :return: A ZIP file containing the isolated speaker audio files.
    """
    try:
        if not request.json or 'youtube_url' not in request.json:
            abort(400, description="Invalid request. Please provide 'youtube_url'.")

        youtube_url = request.json['youtube_url']
        parsed_url = urlparse(youtube_url)
        if not re.match(r'^(www\.)?youtube\.com', parsed_url.netloc):  # Validate the YouTube URL
            abort(400, description="Invalid YouTube URL.")

        api_key = API_KEY
        if not api_key:
            abort(400, description="Missing API key. Please set the ASSEMBLY_AI_API_KEY environment variable.")

        original_audio_bytes = download_youtube_video(youtube_url)  # Download the audio from the YouTube video
        upload_url = upload_audio(original_audio_bytes.getvalue(), api_key)  # Upload the audio to AssemblyAI
        transcript = get_transcript(upload_url, api_key)  # Get the transcript using the AssemblyAI API
        speakers_audio = create_speaker_segments(transcript, original_audio_bytes.getvalue())  # Create speaker segments

        # Create a ZIP file containing the output audio files
        zip_file = io.BytesIO()
        with zipfile.ZipFile(zip_file, 'w') as zipf:
            for speaker_audio_tuple in speakers_audio:
                file_name = speaker_audio_tuple[0]
                audio_data = speaker_audio_tuple[1]
                zipf.writestr(file_name, audio_data.export(format="mp3").read())

        zip_file.seek(0)

        # Return the ZIP file as a downloadable attachment
        return send_file(
            zip_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name='speaker_segments.zip'
        )

    except Exception as e:
        logger.error(f"Error processing video: {str(e)}")
        abort(500, description="An error occurred while processing the video.")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
