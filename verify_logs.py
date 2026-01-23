import requests
import json

def check_api():
    base_url = "http://localhost:5000/api"
    
    print("--- STATUS SUMMARY ---")
    try:
        resp = requests.get(f"{base_url}/status")
        data = resp.json()
        print(f"Total: {data.get('stats', {}).get('total')}")
        print(f"Metrics (New): {data.get('metrics')}")
        print(f"Resources (New): {data.get('resources')}")
        stream = data.get('stream_buffer', '')
        print(f"Stream Buffer First 100 chars: {stream[:100]}")
    except Exception as e:
        print(f"Error fetching status: {e}")

    print("\n--- LOG MESSAGES (Last 5) ---")
    try:
        resp = requests.get(f"{base_url}/logs")
        logs = resp.json()
        # The logs might be strings or objects. Based on code, they seem to be strings in system_logs
        # but let's handle both.
        for log in logs[-5:]:
            print(log)
    except Exception as e:
        print(f"Error fetching logs: {e}")

if __name__ == "__main__":
    check_api()
