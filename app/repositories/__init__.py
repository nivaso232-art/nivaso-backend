import os
import requests

# =========================
# CONFIG
# =========================

ACCESS_TOKEN = "EAAcLFavluI8BSbBmLkpLszdoJNnLOOSlVoWdvTPWnn3tGEc39hTTPVDI3B0BdqTo3ha15C9JBF0jHU9FOhXwJjuJcUXb4Rc0ZAYX19VpB8ZAbHonnQhCEppu6wnGPK4MXOBeODbp7LuugfdQCucCUnknnjGZCZCw5b8Y3cPaxzro8iGlXdJSWiDVrzdseQZDZD"
PHONE_NUMBER_ID = "1299346253266374"

# Number you want to send the message to
# Include country code, without +, spaces, or -
RECIPIENT = "7639490537"

MESSAGE = "Hello! 👋 This message was sent using WhatsApp Cloud API."


# =========================
# VALIDATION
# =========================

if not ACCESS_TOKEN:
    raise ValueError("Missing WHATSAPP_ACCESS_TOKEN")

if not PHONE_NUMBER_ID:
    raise ValueError("Missing WHATSAPP_PHONE_NUMBER_ID")


# =========================
# SEND MESSAGE
# =========================

url = f"https://graph.facebook.com/v23.0/{PHONE_NUMBER_ID}/messages"

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

payload = {
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": RECIPIENT,
    "type": "text",
    "text": {
        "preview_url": False,
        "body": MESSAGE,
    },
}

print("Sending WhatsApp message...")
print("To:", RECIPIENT)

response = requests.post(
    url,
    headers=headers,
    json=payload,
    timeout=30,
)

print("HTTP:", response.status_code)
print("Response:")
print(response.text)

if response.ok:
    data = response.json()

    message_id = data.get("messages", [{}])[0].get("id")

    print("\n✅ Message sent successfully!")

    if message_id:
        print("Message ID:", message_id)

else:
    print("\n❌ Failed to send message.")

