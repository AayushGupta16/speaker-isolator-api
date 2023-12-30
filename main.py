import requests
import time
import json
from firebase_admin import credentials, storage, initialize_app
import io
from pytube import YouTube
from pydub import AudioSegment
from pytube.exceptions import PytubeError
from requests.exceptions import HTTPError, ConnectionError
from firebase_admin.exceptions import FirebaseError

# Initialize Firebase app
cred = credentials.Certificate("./firebase_auth.json")
app = initialize_app(cred, {'storageBucket': 'elevenlabsauto.appspot.com'}, name='storage')


def process_video(api_key, youtube_url, output_filename, use_saved_data, transcript_file_path="transcript.json"):
    try:
        print("Downloading YouTube Video")
        #Download Video
        original_audio_bytes = download_youtube_video_pytube(youtube_url)  # Download the YouTube video's audio
        #Upload the audio to firebase, returns the url
        blob, url = upload_to_firebase(output_filename, original_audio_bytes)
        if not blob or not url:
            print("Error: Failed to upload audio file to Firebase.")
            return None

        #Fetches the transcript
        audio_url = url
        transcript = get_transcript(api_key, audio_url)

        if not transcript:
            print("Error: Transcript data is not available.")
            return None


        #Deletes blob
        delete_from_firebase(blob)

        #Creates array of speaker files, each file should should mp3 bytes
        speaker_files = create_speaker_segments(transcript, original_audio_bytes)

        speaker_files = sorted(speaker_files, key=lambda x: x[0].split('_')[1])  # Sort the speaker files by name
        return speaker_files  # Return the speaker files

    except PytubeError as err:
        print(f"Error: Failed to download YouTube video. {err}")
        return None
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return None


def download_youtube_video_pytube(url):
    try:
        audio_data = io.BytesIO()
        yt = YouTube(url)
        stream = yt.streams.filter(only_audio=True).first()
        # Download the stream and write it to the audio_data buffer
        stream.stream_to_buffer(audio_data)

        # Reset the buffer position to the beginning
        audio_data.seek(0)

        # Convert raw data to MP3
        audio = AudioSegment.from_file(audio_data, format="mp4")
        mp3_data = io.BytesIO()
        audio.export(mp3_data, format="mp3")

        # Reset the buffer position to the beginning
        mp3_data.seek(0)
        return mp3_data

    except Exception as e:
        print(f"Error in downloading or converting YouTube video: {str(e)}")
        return None


def upload_to_firebase(file_name, file_data):
    try:
        bucket = storage.bucket(app=app)
        blob = bucket.blob(file_name)
        blob.upload_from_file(file_data)
        blob.make_public()
        return blob, blob.public_url

    except FirebaseError as err:
        print(f"Error in uploading to Firebase: {err}")
        return None, None


def delete_from_firebase(blob):
    try:
        blob.delete()
    except FirebaseError as err:
        print(f"Error in deleting from Firebase: {err}")


def get_transcript(api_key, audio_url, save_json=True, json_file_path="transcript.json"):
    endpoint = "https://api.assemblyai.com/v2/transcript"
    headers = {"authorization": api_key}
    json_data = {"audio_url": audio_url, "speaker_labels": True}

    try:
        # Make a POST request to the endpoint with the JSON data and headers
        post_response = requests.post(endpoint, json=json_data, headers=headers)
        post_response.raise_for_status()  # Raise an exception for non-2xx response codes
        post_response_json = post_response.json()
        transcript_id = post_response_json['id']  # Get the transcript ID from the response

        while True:
            url = f"{endpoint}/{transcript_id}"  # Create the URL for the GET request
            get_response = requests.get(url, headers=headers).json()  # Get the response JSON data
            status = get_response['status']  # Get the status from the response

            # Handle the different statuses
            if status == 'completed':  # If the transcript is complete
                if save_json:  # If saving the transcript JSON data to a file is enabled
                    with open(json_file_path, 'w') as json_file:  # Open the JSON file for writing
                        json.dump(get_response, json_file)  # Save the JSON data to the file
                return get_response  # Return the transcript JSON data
            elif status == 'processing':  # If the transcript is still processing
                print("Processing...")  # Print a message to indicate processing
                time.sleep(5)  # Sleep for 5 seconds before checking the status again
            else:  # If there's an error or an unexpected status
                print(f"Error: {status}")  # Print an error message with the status
                return None  # Return None to indicate an error

    except HTTPError as err:
        print(f"HTTPError in getting transcript: {err}")
        return None
    except ConnectionError as err:
        print(f"ConnectionError in getting transcript: {err}")
        return None
    except Exception as err:
        print(f"Unexpected error in getting transcript: {err}")
        return None


def get_transcript_from_file(file_path):
    try:
        with open(file_path, "r") as f:  # Open the file for reading
            transcript_data = json.load(f)  # Load the JSON data from the file
        return transcript_data  # Return the transcript JSON data

    except FileNotFoundError:
        print(f"Error: Transcript file not found at '{file_path}'.")
        return None
    except json.JSONDecodeError:
        print(f"Error: Failed to decode JSON from '{file_path}'.")
        return None


def create_speaker_segments(data, audio_bytes):
    try:
        full_audio = AudioSegment.from_file(io.BytesIO(audio_bytes.getvalue()), format='mp3')
        speaker_audios = {}
        output_files = []
        last_end_time = {speaker: 0 for speaker in set(utterance["speaker"] for utterance in data["utterances"])}

        for utterance in sorted(data["utterances"], key=lambda x: x['start']):
            speaker = utterance["speaker"]
            start = max(utterance["start"], last_end_time[speaker])
            end = utterance["end"]
            if start < end:
                utterance_audio = full_audio[start:end]
                if speaker not in speaker_audios:
                    speaker_audios[speaker] = utterance_audio
                else:
                    speaker_audios[speaker] += utterance_audio
            last_end_time[speaker] = max(end, last_end_time[speaker])

        for speaker, speaker_audio in speaker_audios.items():
            max_duration_ms = 5 * 60 * 1000
            speaker_audio_parts = [speaker_audio[i:i + max_duration_ms] for i in range(0, len(speaker_audio), max_duration_ms)]

            for i, part_audio in enumerate(speaker_audio_parts):
                output_files.append((f"Speaker_{speaker}_Part_{i + 1}", part_audio))

        return output_files

    except Exception as e:
        print(f"Error in creating speaker segments: {str(e)}")
        return []
