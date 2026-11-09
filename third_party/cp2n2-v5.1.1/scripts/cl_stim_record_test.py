from dotenv import load_dotenv
load_dotenv()

import time
import cl


def main():
    print("Opening CL API...")
    with cl.open() as neurons:
        attrs = cl.get_system_attributes()
        print("Connected.")
        print("System attributes keys:", sorted(attrs.keys()))
        print("Channel count:", neurons.get_channel_count())
        print("FPS:", neurons.get_frames_per_second())
        print("Frame duration (us):", neurons.get_frame_duration_us())

        print("Starting recording...")
        recording = neurons.record(stop_after_seconds=2)

        time.sleep(0.2)

        print("Stimulating channel 1 ...")
        neurons.stim(1, 0.4)

        recording.wait_until_stopped()
        print("Recording finished.")


if __name__ == "__main__":
    main()