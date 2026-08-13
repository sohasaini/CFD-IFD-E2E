# E2E AI Automation Analyzer

Flask application for Agentic AI E2E validation. The app reads CFD data, finds related automation coverage, triggers validation workflow logic, monitors execution evidence, classifies results, and generates HTML reports.

## Folder Layout

This application and the automation repository are separate folders.

Recommended Windows layout:
C:\Automation
```

Recommended Linux/macOS layout:

/opt/automation
```

`AUTOMATION_PATH` must point to the automation repository root. That folder should contain automation folders such as:

```text
TestSuites/
Tests/
```

## Windows Setup

From PowerShell:

```powershell
cd C:\E2E-AI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Place or clone the automation codebase here by default:

```text
C:\Automation
```

If the automation codebase is somewhere else, update `AUTOMATION_PATH` in `.env`.

## Linux/macOS Setup

```bash
cd /opt/e2e-ai
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Place or clone the automation codebase, for example:

```text
/opt/automation
```

Then update `AUTOMATION_PATH` in `.env`.

## Environment Configuration

Create or update `.env` in the project root.

Local Windows example:

```env
FLASK_SECRET_KEY=change-this-secret
FLASK_DEBUG=true
APP_HOST=127.0.0.1
APP_PORT=5005
AUTOMATION_PATH=C:\Automation

AI_BASE_URL=
AI_API_KEY=
AI_MODEL=

CISCO_AI_CLIENT_ID=your-client-id
CISCO_AI_CLIENT_SECRET=your-client-secret
CISCO_AI_APP_KEY=your-app-key
CISCO_AI_TOKEN_URL=https://id.cisco.com/oauth2/default/v1/token
CISCO_AI_ENDPOINT=https://chat-ai.cisco.com
CISCO_AI_API_VERSION=2025-04-01-preview
CISCO_AI_MODEL=gpt-5-nano

CDETS_CONSUMER_KEY=your-cdets-consumer-key
CDETS_CONSUMER_SECRET=your-cdets-consumer-secret

AI_RECOMMENDATION_LIMIT=10
AI_INDEX_CANDIDATE_LIMIT=100
```

Server example:

```env
FLASK_SECRET_KEY=change-this-secret
FLASK_DEBUG=false
APP_HOST=0.0.0.0
APP_PORT=5005
AUTOMATION_PATH=/opt/automation
```

Important: if `APP_HOST`, `APP_PORT`, or `AUTOMATION_PATH` are already set in the OS/session environment, those values can override `.env`.

Check on Windows:

```powershell
echo $env:APP_HOST
echo $env:APP_PORT
echo $env:AUTOMATION_PATH
```

Temporarily set for current PowerShell session:

```powershell
$env:APP_HOST = "0.0.0.0"
$env:APP_PORT = "5005"
$env:AUTOMATION_PATH = "C:\Automation"
```

Remove current PowerShell session overrides so `.env` is used:

```powershell
Remove-Item Env:APP_HOST
Remove-Item Env:APP_PORT
Remove-Item Env:AUTOMATION_PATH
```

## Run The App

Windows:

```powershell
cd C:\E2E-AI
.\.venv\Scripts\Activate.ps1
python app.py
```

Linux/macOS:

```bash
cd /opt/e2e-ai
source .venv/bin/activate
python app.py
```

Open locally:

```text
http://127.0.0.1:5005
```

If hosted on a server with `APP_HOST=0.0.0.0`, open from another machine:

```text
http://SERVER_PUBLIC_IP:5005
```


## Active UI

The root route `/` opens the Agentic Dashboard:

```text
http://127.0.0.1:5005/
```
