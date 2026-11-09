import requests
import os


def send_sms(phone_number, message):
    api_key = os.environ.get("AT_API_KEY")
    username = os.environ.get("AT_USERNAME", "sandbox")

    if username == "sandbox":
        url = "https://api.sandbox.africastalking.com/version1/messaging"
    else:
        url = "https://api.africastalking.com/version1/messaging"

    headers = {
        "apiKey": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    payload = {"username": username, "to": str(phone_number), "message": message}

    requests.post(url, headers=headers, data=payload)
