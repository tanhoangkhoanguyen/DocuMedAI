import subprocess

# Launch Streamlit via subprocess for proper runtime isolation
def main():
    cmd = [
        "streamlit", "run", "app.py",
        "--server.port=9005",
        "--server.address=0.0.0.0"
    ]
    subprocess.run(cmd)

if __name__ == "__main__":
    main()