from dotenv import load_dotenv
load_dotenv()

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

if __name__ == "__main__":
    main()