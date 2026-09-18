# Report Delivery Setup

The portfolio monitor generates two report formats:
- **Long report** (`*_email_*.txt`): Detailed analysis for email
- **Short report** (`*_whatsapp_*.txt`): Concise summary for WhatsApp

## Email Delivery

### Option 1: Manual (Simplest)
1. Run the portfolio monitor
2. Open the generated `*_email_*.txt` file
3. Copy content into your email client
4. Send to yourself or distribution list

### Option 2: Command Line (Linux/Mac)
```bash
# Using mail command
cat portfolio_report_email_20260201.txt | mail -s "Daily Portfolio Report" you@email.com

# Using sendmail
sendmail you@email.com < portfolio_report_email_20260201.txt
```

### Option 3: Python SMTP Script
Create a `send_email.py` wrapper:

```python
import smtplib
from email.mime.text import MIMEText
import sys

# Configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER = "your.email@gmail.com"
PASSWORD = "your_app_password"  # Use App Password for Gmail
RECIPIENT = "your.email@gmail.com"

def send_report(report_file):
    with open(report_file, 'r') as f:
        content = f.read()

    msg = MIMEText(content)
    msg['Subject'] = 'Daily Portfolio Report'
    msg['From'] = SENDER
    msg['To'] = RECIPIENT

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER, PASSWORD)
        server.send_message(msg)

if __name__ == '__main__':
    send_report(sys.argv[1])
```

Usage: `python send_email.py portfolio_report_email_20260201.txt`

### Gmail Setup
1. Enable 2-Factor Authentication
2. Create App Password: Google Account → Security → App Passwords
3. Use App Password in script (not your regular password)

## WhatsApp Delivery

### Option 1: Manual Copy-Paste (Recommended)
1. Open the `*_whatsapp_*.txt` file
2. Copy the content
3. Paste into WhatsApp (self-chat or group)

### Option 2: WhatsApp Web Automation
Use Selenium or similar to automate pasting:
1. Open WhatsApp Web in browser
2. Navigate to target chat
3. Paste message content

### Option 3: Twilio WhatsApp API (Business)
Requires Twilio account and WhatsApp Business API approval.

```python
from twilio.rest import Client

account_sid = 'your_account_sid'
auth_token = 'your_auth_token'
client = Client(account_sid, auth_token)

with open('portfolio_report_whatsapp_20260201.txt', 'r') as f:
    message_body = f.read()

message = client.messages.create(
    from_='whatsapp:+14155238886',  # Twilio sandbox number
    body=message_body,
    to='whatsapp:+1234567890'  # Your number
)
```

### Option 4: WhatsApp Click-to-Chat
Generate a pre-filled WhatsApp link:
```
https://wa.me/YOURNUMBER?text=YOUR_ENCODED_MESSAGE
```

## Automation with Cron (Linux/Mac)

Schedule daily reports:

```bash
# Edit crontab
crontab -e

# Add line to run at 6 PM every weekday
0 18 * * 1-5 cd /path/to/asset-take && python portfolio_monitor.py -p positions.csv -o reports/daily
```

## Automation with Task Scheduler (Windows)

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger: Daily at desired time
4. Action: Start a program
5. Program: `python`
6. Arguments: `portfolio_monitor.py -p positions.csv`
7. Start in: Path to skill directory
