"""
InnerJoy Marketing Agent - Backend
====================================
Requirements:
    pip install flask flask-cors anthropic openpyxl
    pip show ***
"""

import json
import smtplib
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
import openpyxl
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
load_dotenv()  # Load configuration from .env file

from flask import send_from_directory

# ─────────────────────────────────────────────
#  CONFIGURATION  — fill these in
# ─────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

GMAIL_SENDER     = os.environ.get("GMAIL_SENDER")     # Gmail address used to send emails
GMAIL_PASSWORD   = os.environ.get("GMAIL_PASSWORD")   # Gmail App Password (NOT gmail normal password)
                                        # Get it at: myaccount.google.com -> Security -> App Passwords

EXCEL_FILE       = "email_log.xlsx"    # Log file name (created automatically)

# ─────────────────────────────────────────────
#  FLASK APP
# ─────────────────────────────────────────────

app = Flask(__name__)
CORS(app)  # Allow the HTML page to call this server

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Conversation history — kept in memory while server is running
conversation_history = []

# ─────────────────────────────────────────────
#  SYSTEM PROMPT
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """
You are the InnerJoy Marketing Agent — an expert assistant for InnerJoy Ed's marketing and outreach team.

## WHO IS INNERJOY?

InnerJoy Ed is an AI-powered K-5 Social-Emotional Learning (SEL) platform.
Positioning: "Second Step for the AI generation."

Mission: Give every child, regardless of background, portable emotional skills they can use for life.

Core belief: Children are not broken — they are overwhelmed. When emotional skills are taught
through practice, embodiment, and safety, children don't just behave better — they feel better.

## OUR PRODUCT
- PCS (Portable Coping Skills): Named tools children actually use in real life.
  Examples: Thought Detective, Body Compass, Triangle Breathing.
- Curriculum: K-5, 34 lessons per grade, 11 modules, 1 PCS per module.
- Format: 15–25 minute lessons with segments: Let's Breathe, Let's Talk, Let's Level Up, Let's Reflect, Let's Get Zen.
- 1,200+ videos, Netflix-quality animation, therapist-led content.
- AI-powered: 300+ curriculum variations, individualized reports, real-time dashboards.

## KEY PROOF POINTS (mention these when writing outreach)
- 65% average SEL growth across pilots
- $1.2M saved for one Chicago school district (zero children sent to alternative placement)
- 800+ students across US pilots
- MIT-validated results
- CASEL-aligned (the language districts use)

## AUDIENCE MESSAGING
- Superintendents: lead with $1.2M savings, board-ready data, CASEL, MIT, turnkey implementation
- Counselors: lead with clinical accuracy (CBT, ACT, DBT), precision data per student
- Teachers: lead with ready 25-min lessons, low prep, engaging videos
- Parents: lead with visible progress, weekly updates, tips to reinforce at home
- Students: fun characters, build your own digital toolbox

## BRAND VOICE
Warm, confident, science-backed, human. Plain language. Lead with the child, not the product.

## YOUR TOOLS
1. web_search — search the internet for prospect info, school districts, contacts, SEL news
2. send_email — send a real email via Gmail SMTP. Always show the draft to the user first and ask for confirmation before sending.
3. save_to_log — save email activity to the Excel log file

## RULES
- Always match email tone to stakeholder type
- Always include at least one proof point in outreach emails
- Before calling send_email, show the draft and ask the user to confirm
- Keep cold outreach emails under 200 words
"""

# ─────────────────────────────────────────────
#  TOOLS DEFINITION  (sent to Claude)
# ─────────────────────────────────────────────

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the internet for information about school districts, superintendents, "
            "principals, SEL news, or any prospect research."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Example: 'superintendent Chicago Public Schools 2025'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "send_email",
        "description": (
            "Send an email via Gmail SMTP. Only call this AFTER the user has confirmed the draft. "
            "Automatically logs the action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to":            {"type": "string", "description": "Recipient email address"},
                "subject":       {"type": "string", "description": "Email subject line"},
                "body":          {"type": "string", "description": "Full email body (plain text)"},
                "recipient_name":{"type": "string", "description": "Name or role of the recipient"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    {
        "name": "save_to_log",
        "description": "Save a record of an email activity to the Excel log file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sent_to":        {"type": "string", "description": "Recipient email"},
                "recipient_name": {"type": "string", "description": "Recipient name or role"},
                "subject":        {"type": "string", "description": "Email subject"},
                "status":         {"type": "string", "description": "Status: Sent, Draft, Failed"},
                "notes":          {"type": "string", "description": "Any notes"}
            },
            "required": ["sent_to", "subject", "status"]
        }
    }
]

# ─────────────────────────────────────────────
#  TOOL EXECUTORS
# ─────────────────────────────────────────────

def execute_web_search(query: str) -> dict:
    """
    Uses a second Claude call with the built-in web_search tool.
    Returns the text result.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": f"Search for: {query}. Return the key facts: names, emails if found, school info, source URLs."
            }]
        )
        # Collect text blocks from response
        result = " ".join(
            block.text for block in response.content
            if hasattr(block, "text") and block.text
        )
        return {"result": result or "No results found.", "query": query}
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}


def execute_send_email(to: str, subject: str, body: str, recipient_name: str = "") -> dict:
    """
    Sends a real email using Gmail SMTP.
    Also saves to the Excel log.
    """
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, to, msg.as_string())

        # Log the sent email
        execute_save_to_log(
            sent_to=to,
            recipient_name=recipient_name,
            subject=subject,
            status="Sent",
            notes="Sent via Gmail SMTP"
        )

        return {"success": True, "message": f"Email sent to {to}."}

    except Exception as e:
        execute_save_to_log(
            sent_to=to,
            recipient_name=recipient_name,
            subject=subject,
            status="Failed",
            notes=str(e)
        )
        return {"error": f"Failed to send email: {str(e)}"}


def execute_save_to_log(
    sent_to: str,
    subject: str,
    status: str,
    recipient_name: str = "",
    notes: str = ""
) -> dict:
    try:
        print(f"DEBUG save_to_log: saving to {EXCEL_FILE}")  # ← add this to explore silent failing
        
        if os.path.exists(EXCEL_FILE):
            wb = openpyxl.load_workbook(EXCEL_FILE)
            ws = wb.active
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Email Log"
            ws.append(["Date & Time", "Sent By", "Sent To", "Recipient Name", "Subject", "Status", "Notes"])

        ws.append([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            GMAIL_SENDER,
            sent_to,
            recipient_name,
            subject,
            status,
            notes
        ])

        wb.save(EXCEL_FILE)
        print(f"DEBUG save_to_log: saved successfully")  # ← add this to explore silent failing
        return {"success": True, "message": f"Saved to {EXCEL_FILE}"}

    except Exception as e:
        print(f"DEBUG save_to_log ERROR: {str(e)}")  # ← add this to explore silent failing
        return {"error": f"Failed to save log: {str(e)}"}


def run_tool(tool_name: str, tool_input: dict) -> dict:
    """Routes tool calls to the right executor."""
    if tool_name == "web_search":
        return execute_web_search(tool_input["query"])

    elif tool_name == "send_email":
        return execute_send_email(
            to=tool_input["to"],
            subject=tool_input["subject"],
            body=tool_input["body"],
            recipient_name=tool_input.get("recipient_name", "")
        )

    elif tool_name == "save_to_log":
        return execute_save_to_log(
            sent_to=tool_input["sent_to"],
            subject=tool_input["subject"],
            status=tool_input["status"],
            recipient_name=tool_input.get("recipient_name", ""),
            notes=tool_input.get("notes", "")
        )

    else:
        return {"error": f"Unknown tool: {tool_name}"}

# ─────────────────────────────────────────────
#  AGENT LOOP
# ─────────────────────────────────────────────

def agent_loop(user_message: str):
    """
    Adds the user message to history, then runs the agentic loop:
    Claude thinks → calls tools → gets results → continues until done.

    Returns:
        reply        (str)  — final text response to show the user
        tool_actions (list) — short descriptions of tools that were called
    """
    global conversation_history

    conversation_history.append({
        "role": "user",
        "content": user_message
    })

    tool_actions = []

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=conversation_history
        )

        # Collect tool_use blocks and text blocks
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        text_blocks  = [b for b in response.content if b.type == "text"]

        # If no tool calls → we are done
        if not tool_blocks:
            final_text = " ".join(b.text for b in text_blocks if b.text)
            conversation_history.append({
                "role": "assistant",
                "content": response.content
            })
            return final_text, tool_actions

        # Save assistant message (includes tool_use blocks)
        conversation_history.append({
            "role": "assistant",
            "content": response.content
        })

        # Execute each tool
        tool_results = []
        for block in tool_blocks:
            tool_name  = block.name
            tool_input = block.input

            # Record for the frontend
            tool_actions.append(f"Tool called: {tool_name} — input: {json.dumps(tool_input)[:120]}")

            # Run the tool
            result = run_tool(tool_name, tool_input)

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result)
            })

        # Add tool results back to history so Claude can continue
        conversation_history.append({
            "role": "user",
            "content": tool_results
        })

        # Loop again — Claude will now process the tool results

# ─────────────────────────────────────────────
#  FLASK ROUTES
# ─────────────────────────────────────────────

@app.route(/)
def index():
    return send_from_directory(".", "index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    try:
        reply, tool_actions = agent_loop(user_message)
        return jsonify({
            "reply":        reply,
            "tool_actions": tool_actions
        })
    # except Exception as e:
    #     return jsonify({"error": str(e)}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    """Clears conversation history."""
    global conversation_history
    conversation_history = []
    return jsonify({"message": "Conversation reset."})


# ─────────────────────────────────────────────
#  START SERVER
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("InnerJoy Marketing Agent running at http://localhost:5000")
    print("Open index.html in your browser.")
    # app.run(debug=True, port=5000) #← use this to get detailed error messages in the console
    port = int(os.environ.get("PORT", 5000)) #← use PORT env var if available (for deployment), otherwise default to 5000
    app.run(debug=False, host="0.0.0.0", port=port)