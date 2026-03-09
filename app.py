from edslack import EdSlackAPI
import sys
import time
from pytz import timezone
from functools import wraps
sys.path.append('..')

from course_constants import *
import getpass
from gradescope_api.client import GradescopeClient
from gradescope_api.course import GradescopeCourse

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import matplotlib
from scipy.stats import percentileofscore
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from datetime import datetime, timedelta
import yaml
import os
import io
import base64
import gspread
import requests
import numpy as np
from bcourses import CanvasClient 
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import pytz
import threading


# Edstem Tracker
ALERT_CHANNEL = "#ed"  # Replace with your real Slack channel
NOTIFY_HOURS = (8, 23)  # Only send pings between 08:00–23:59
SLACK_TEXT_LIMIT = 3500

# Google Sheets API
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SERVICE_ACCOUNT_FILE = "gspread-api.json"
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
service = build('sheets', 'v4', credentials=creds)
SPREADSHEET_ID = "1N9Ep22MZJPwGEcfdbOiCSd93oVr2A6iKSXibaGyUFs4"
EXTENSIONS_ID = "1V0hP7au5CyO9mWK9X2IeH4z0aqxJaeXJz5qz8EgGS8A"
EXTENSIONS_RANGE = "Form Responses!A1:N"


with open("config/credentials.yml", 'r') as stream:
    try:
        credentials = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

# Per-workspace Slack: built from credentials.courses.<key>.slack
SLACK_WORKSPACES = {
    k: v.get("slack", {}) for k, v in COURSES.items() if v.get("slack")
}

# Populated in main(): team_domain -> Bolt App (used by scheduler to post per-workspace)
slack_app_by_team = {}


def _get_course_config_for_command(command: dict) -> dict:
    """
    Helper to resolve per-course configuration based on the Slack team_domain.
    """
    team_domain = command.get("team_domain")
    return get_course_config(team_domain)

def log_command(command_name):
    def decorator(func):
        @wraps(func)
        def wrapper(ack, say, command, *args, **kwargs):
            ack()
            user = command.get("user_name", "unknown_user")
            command_text = command.get("text", "").strip() or "(no input)"
            timestamp = datetime.now(timezone("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S")
            start_time = time.perf_counter()

            say(f"Message received ({timestamp}) from @{user}: `{command_name} {command_text}`")

            try:
                result = func(ack, say, command, *args, **kwargs)
                elapsed = round((time.perf_counter() - start_time) * 1000)
                say(f"⏱️ Command `{command_name}` completed successfully, response took `{elapsed}ms`.")
                return result
            except Exception as e:
                elapsed = round((time.perf_counter() - start_time) * 1000)
                say(f"❌ Error in `{command_name}` after `{elapsed}ms`: {e}")
                raise e
        return wrapper
    return decorator

def _to_local_timestamp(raw_value):
    timestamp = pd.to_datetime(raw_value, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return None
    return timestamp.tz_convert("America/Los_Angeles")

def _format_elapsed_from_hours(hours_passed):
    days = int(hours_passed // 24)
    hours = int(hours_passed % 24)
    minutes = int((hours_passed * 60) % 60)

    if days >= 1 and hours >= 1:
        return f"{days}d {hours}h unanswered"
    if days >= 1:
        return f"{days}d unanswered"
    if hours >= 1:
        return f"{hours}h unanswered"
    return f"{minutes}m unanswered"

def _format_relative_time(created_str, now):
    created_time = _to_local_timestamp(created_str)
    if created_time is None:
        return "unknown time"
    hours_passed = max((now - created_time).total_seconds() / 3600, 0)
    return _format_elapsed_from_hours(hours_passed).replace(" unanswered", " ago")

def _format_range_from_timestamps(timestamps, now):
    if not timestamps:
        return "time unavailable"
    if len(timestamps) == 1:
        return _format_relative_time(timestamps[0].isoformat(), now)
    oldest = _format_relative_time(timestamps[0].isoformat(), now)
    newest = _format_relative_time(timestamps[-1].isoformat(), now)
    return f"{newest} to {oldest}"

def _split_long_text(text, max_len=SLACK_TEXT_LIMIT):
    if len(text) <= max_len:
        return [text]

    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= max_len:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        if len(line) <= max_len:
            current = line
        else:
            start = 0
            while start < len(line):
                chunks.append(line[start : start + max_len])
                start += max_len

    if current:
        chunks.append(current)
    return chunks

def _chunk_sections(sections, max_len=SLACK_TEXT_LIMIT):
    chunks, current = [], ""
    for section in sections:
        section_parts = _split_long_text(section, max_len=max_len)
        for part in section_parts:
            candidate = part if not current else f"{current}\n\n{part}"
            if len(candidate) <= max_len:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = part

    if current:
        chunks.append(current)
    return chunks

def _build_unresolved_posts(ed_slack, ed_course_id, now):
    unresolved_threads = ed_slack.filtered_threads(ed_slack.session, "unresolved") or []
    if not unresolved_threads:
        return []

    processed = ed_slack.process_json(unresolved_threads, ed_slack.fields)
    overdue_posts = []

    for _, row in processed.iterrows():
        post_id = row.get("id")
        if not post_id:
            continue

        title = row.get("title", "Untitled")
        created_time = _to_local_timestamp(row.get("created_at"))
        post_url = f"https://edstem.org/us/courses/{ed_course_id}/discussion/{post_id}"
        unresolved_count = row.get("unresolved_count")
        if pd.isna(unresolved_count):
            unresolved_count = 0
        try:
            unresolved_count = int(unresolved_count)
        except (TypeError, ValueError):
            unresolved_count = 0

        thread_user = row.get("user", {})
        is_student_thread = isinstance(thread_user, dict) and thread_user.get("course_role") == "student"
        timestamps = []
        try:
            if is_student_thread:
                # For student-authored question threads, use thread post time only.
                timestamps = [created_time] if created_time is not None else []
            else:
                timestamps = ed_slack.get_unresolved_activity_timestamps(
                    post_id,
                    unresolved_count=unresolved_count,
                    student_thread=False,
                )
            range_text = _format_range_from_timestamps(timestamps, now)
        except Exception:
            range_text = "time unavailable"

        oldest_activity_hours = None
        if timestamps:
            oldest_activity_hours = max((now - timestamps[0]).total_seconds() / 3600, 0)
        elif created_time is not None:
            oldest_activity_hours = max((now - created_time).total_seconds() / 3600, 0)
        else:
            oldest_activity_hours = -1

        # Ignore false positives where the unresolved filter includes zero unresolved activity.
        effective_unresolved_count = unresolved_count if unresolved_count > 0 else len(timestamps)
        if effective_unresolved_count <= 0:
            continue

        overdue_posts.append(
            {
                "post_id": post_id,
                "title": title,
                "url": post_url,
                "hours_passed": oldest_activity_hours,
                "unresolved_count": effective_unresolved_count,
                "range_text": range_text,
            }
        )

    overdue_posts.sort(key=lambda post: post["hours_passed"], reverse=True)
    return overdue_posts

def _render_unresolved_sections(overdue_posts):
    sections = []
    for post in overdue_posts:
        window_label = f"unresolved activity window: {post['range_text']}"
        if post["unresolved_count"] == 1:
            sections.append(f"*<{post['url']}|{post['title']}>* | {window_label}")
        else:
            sections.append(
                f"*<{post['url']}|{post['title']}>* | {post['unresolved_count']} unresolved | {window_label}"
            )
    return sections

def _post_threaded_sections(client, channel_id, header_message, sections):
    header_response = client.chat_postMessage(channel=channel_id, text=header_message)
    thread_ts = header_response["ts"]
    # Keep one Slack reply per post for easier TA scanning.
    for section in sections:
        for chunk in _split_long_text(section):
            client.chat_postMessage(
                channel=channel_id,
                text=chunk,
                thread_ts=thread_ts,
                unfurl_links=False,
                unfurl_media=False,
            )
    return thread_ts

def register_handlers(app):
    @app.event("app_mention")
    def event_test(event, say):
        print(f"Received event: {event}")  # Debugging log
        say("Hi there!")

    @app.command("/current_unresolved")
    def unresolved_info(ack, say, command, client):
        ack()
        edSlack = EdSlackAPI(command["team_domain"])

        try:
            course_config = _get_course_config_for_command(command)
            ed_course_id = course_config["edstem"]["ED_COURSE_ID"]
            now = datetime.now(pytz.timezone("America/Los_Angeles"))
            overdue_posts = _build_unresolved_posts(edSlack, ed_course_id, now)

            if not overdue_posts:
                say("✅ No unresolved threads found!")
                return

            header_message = (
                f":rotating_light: *Unanswered Ed Posts That Need Attention!*\n"
                "React with :white_check_mark: when resolving a thread."
            )
            sections = _render_unresolved_sections(overdue_posts)
            _post_threaded_sections(client, command["channel_id"], header_message, sections)

        except Exception as e:
            print(f"❌ Error in /current_unresolved: {e}")
            say(f"❌ Error processing unresolved threads: {e}")

    @app.command("/top_questions")
    def top_questions(ack, say, command):
        ack()
        edSlack = EdSlackAPI(command['team_domain'])
        date = command["text"]
        month, day, year = date.split("/")
        up_to = datetime(int(year), int(month), int(day))
        upto_threads = edSlack.process_user(edSlack.get_timeframe(up_to))
        upto_questions = upto_threads[upto_threads["type"] == "question"]
        response = f"Hi {command['user_name']}! Here are the top question categories after {date}:\n" + str(upto_questions["category"].value_counts()[:5])
        say(response)

    @app.command("/get_extension")
    def get_extension(ack, say, command):
        ack()
        assignment = command["text"].strip()  # Remove extra spaces
        print(f"🔍 Checking extension for: '{assignment}'")

        try:
            course_config = _get_course_config_for_command(command)
            course_id = course_config.get("gradescope_id") or COURSE_ID
            GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)
            assignment_list = GC.get_assignments()  # Get all assignments
            print(f"📌 Available assignments: {list(assignment_list.keys())}")  # Debugging
            # Try different variations of input (spaces, underscores)
            if assignment not in assignment_list:
                # Try normalizing the input
                normalized_assignment = assignment.replace("_", " ")  # Convert "Lab4" -> "Lab 4"
                if normalized_assignment in assignment_list:
                    assignment = normalized_assignment
                else:
                    say(f"⚠️ Assignment '{assignment}' not found! Available assignments: {', '.join(assignment_list.keys())}")
                    return

            df = GC.get_extensions(assignment)
            df_str = df.to_string()
            response = f"Hi {command['user_name']}! Here are the extension details for {assignment}:\n{df_str}"
            say(response)
        except KeyError as e:
            say(f"⚠️ Error: {e}")
        except Exception as e:
            print(f"❌ Error in /get_extension: {e}")
            say(f"An error occurred: {e}")

    @app.command("/get_user_id")
    def get_user_id(ack, say, command):
        ack()
        say("🔍 Fetching user ID... Please wait.")
        input_text = command["text"]
        parsed_values = input_text.split(",")

        if len(parsed_values) != 3:
            say(f"⚠️ Invalid input format. Use: `/get_user_id Name,Email,SID`")
            return

        Name, email, SID = parsed_values

        course_config = _get_course_config_for_command(command)
        course_id = course_config.get("gradescope_id") or COURSE_ID
        GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)

        if Name.lower() == 'none' and email.lower() == 'none' and SID.lower() == 'none':
            df = GC.get_student_id(None, None, None)
            response = f"Hi {command['user_name']}! Here are the Gradescope user ID details for course {course_id}:\n{df}"
        else:
            id = None
            if Name.lower() != 'none':
                id = GC.get_student_id(Name, None, None)
            elif email.lower() != 'none':
                id = GC.get_student_id(None, email, None)
            elif SID.lower() != 'none':
                id = GC.get_student_id(None, None, SID)

            if "Error" in id:
                response = f"⚠️ {id}"
            else:
                response = f"Hi {command['user_name']}! Here is the Gradescope user ID for {Name or email or SID}:\n{id}"

        say(response)

    @app.command("/get_user_info")
    def get_user_info(ack, say, command):
        ack()
        say(":mag: Fetching student info... Please wait.")

        input_text = command["text"].strip()

        # Ensure input is provided
        if not input_text:
            say("⚠️ Please provide a name, email, or SID. Example: `/get_user_info John Doe`")
            return

        # Connect to Gradescope API
        course_config = _get_course_config_for_command(command)
        course_id = course_config.get("gradescope_id") or COURSE_ID
        GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)

        try:
            # Assume get_student_id exists and returns a DataFrame
            student_info = None

            if "@" in input_text:  # Input is an email
                student_info = GC.get_student_id(None, input_text, None)
            elif input_text.isdigit():  # Input is a student ID (SID)
                student_info = GC.get_student_id(None, None, input_text)
            else:  # Assume it's a name
                student_info = GC.get_student_id(input_text, None, None)

            # Check if any student info was found
            if student_info is None or isinstance(student_info, str):
                say(f"⚠️ No student found matching `{input_text}`.")
                return

            # Extract student information
            response = f"✅ Student Info for `{input_text}`:\n"
            if isinstance(student_info, pd.DataFrame) and not student_info.empty:
                for _, row in student_info.iterrows():
                    response += f"👤 Name: {row.get('Name', 'N/A')}\n📧 Email: {row.get('Email', 'N/A')}\n🆔 SID: {row.get('SID', 'N/A')}\n\n"
            else:
                response += "⚠️ No matching student found."

            say(response)

        except Exception as e:
            print(f"❌ Error in /get_user_info: {e}")
            say(f"⚠️ Error fetching student info: {e}")

    @app.command("/get_student_performance")
    def get_student_performance(ack, say, command):
        ack()
        name = command["text"]
        course_config = _get_course_config_for_command(command)
        course_id = course_config.get("gradescope_id") or COURSE_ID
        GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)

        result = GC.get_student_performance(name)
        response = f"Hi {command['user_name']}! Here are the approximate performance for REDACTED\n{result}"
        say(response)

    @app.command("/refresh_gradescope")
    def refresh_gradescope(ack, say, command):
        global cached_performance_df
        ack()
        course_config = _get_course_config_for_command(command)
        course_id = course_config.get("gradescope_id") or COURSE_ID
        GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)

        GC.update_performance_data()
        response = f"Hi {command['user_name']}! Gradescope Data Had Updated Successfully!"
        say(response)

    @app.command("/plot_questions")
    def plot_questions(ack, say, command, client):
        ack()
        say(":bar_chart: Generating a plot of student questions...")

        try:
            edSlack = EdSlackAPI(command['team_domain'])
            input_text = command["text"].strip().lower()

            # Determine date range
            if input_text == "last week":
                start_date = datetime.now() - timedelta(days=7)
            elif input_text == "last month":
                start_date = datetime.now() - timedelta(days=30)
            else:
                try:
                    month, day, year = input_text.split("/")
                    start_date = datetime(int(year), int(month), int(day))
                except ValueError:
                    say("⚠️ Invalid date format. Use MM/DD/YYYY, 'last week', or 'last month'.")
                    return

            # Fetch and filter student questions
            threads = edSlack.process_user(edSlack.get_timeframe(start_date))
            questions = threads[threads["type"] == "question"]

            # Check if data is available
            if questions.empty:
                say(f"⚠️ No student questions found since {start_date.strftime('%m/%d/%Y')}.")
                return

            # Ensure 'created_at' column exists
            if "created_at" not in questions.columns:
                say(f"⚠️ Error: 'created_at' column not found! Available columns: {', '.join(questions.columns)}")
                return

            # Convert 'created_at' to datetime
            questions.loc[:, "created_at"] = pd.to_datetime(questions["created_at"])

            # Count questions per day
            question_counts = questions.resample("D", on="created_at").size()

            # Generate the plot
            plt.figure(figsize=(10, 5))
            plt.plot(question_counts.index, question_counts.values, marker="o", linestyle="-", label="Student Questions")
            plt.xlabel("Date")
            plt.ylabel("Number of Questions")
            plt.title(f"Student Questions from {start_date.strftime('%m/%d/%Y')} to Today")
            plt.xticks(rotation=45)
            plt.legend()
            plt.grid(True)

            # Save plot to a buffer
            buf = io.BytesIO()
            plt.savefig(buf, format="png", bbox_inches="tight")
            buf.seek(0)

            # Upload plot to Slack
            response = client.files_upload_v2(
                channel=command["channel_id"],
                file=buf,
                filename="questions_plot.png",
                title=f"Student Questions from {start_date.strftime('%m/%d/%Y')} to Today"
            )

            # Confirm success
            if response["ok"]:
                say(f"✅ Here is the student questions plot from {start_date.strftime('%m/%d/%Y')} to today.")
            else:
                say("⚠️ Failed to upload the plot.")

        except Exception as e:
            print(f"❌ Error in /plot_questions: {e}")
            say(f"⚠️ Error generating plot: {e}")

    @app.command("/lab_attendance")
    def lab_attendance(ack, say, command):
        ack()
        input_name = command["text"].strip().lower()

        try:
            # Load attendance sheet from Google Sheets
            creds = Credentials.from_service_account_file("gspread-api.json", scopes=SCOPES)
            service = build('sheets', 'v4', credentials=creds)
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID, range='Form Responses 1!A1:G'
            ).execute()
            rows = result.get("values", [])
            df = pd.DataFrame(rows[1:], columns=rows[0])

            # Combine First + Last Name for comparison
            df["Full Name"] = (df["First Name"].str.strip() + " " + df["Last Name"].str.strip()).str.lower()

            # Match student by full name (case insensitive)
            matched = df[df["Full Name"].str.contains(input_name)]

            if matched.empty:
                say(f"⚠️ Couldn't find attendance info for '{input_name}'")
                return
            elif len(matched["Full Name"].unique()) > 1:
                options = ", ".join(matched['Full Name'].unique()[:5])  # just in case there are too many
                say(f"⚠️ Found multiple matches: {options}. Be more specific.")
                return

            student_name = matched["Full Name"].iloc[0].title()
            attendance_count = matched.shape[0]
            say(f"✅ {student_name} has attended {attendance_count} lab(s).")

        except Exception as e:
            say(f"❌ Error accessing attendance: {e}")

    @app.command("/extensions_count")
    def extensions_count(ack, say, command):
        ack()
        query = command["text"].strip().lower()

        say(f"🔍 Fetching extension request count for: `{query}`...")

        try:
            creds = Credentials.from_service_account_file("gspread-api.json", scopes=SCOPES)
            service = build('sheets', 'v4', credentials=creds)
            result = service.spreadsheets().values().get(
                spreadsheetId=EXTENSIONS_ID, range=EXTENSIONS_RANGE
            ).execute()
            rows = result.get("values", [])
            if not rows:
                say("⚠️ No extension data found.")
                return
            df = pd.DataFrame(rows[1:], columns=rows[0])
            df.columns = df.columns.str.strip()  # Clean up any spacing

            match = df[
                df["Email Address"].str.lower().str.contains(query) |
                df["Student ID Number"].str.lower().str.contains(query)
            ]
            count = match.shape[0]
            if count == 0:
                say(f"❌ No extension requests found for `{query}`.")
            else:
                say(f"📬 `{query}` has submitted {count} extension request(s).")

        except Exception as e:
            print(f"❌ Error in /extensions_count: {e}")
            say(f"❌ Error accessing extension data: {e}")

    @app.command("/plot_attendance")
    def plot_attendance(ack, say, command, client):
        ack()
        say(":bar_chart: Generating attendance plot by section...")

        try:
            # Load attendance sheet from Google Sheets
            creds = Credentials.from_service_account_file("gspread-api.json", scopes=SCOPES)
            service = build('sheets', 'v4', credentials=creds)
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID, range='Form Responses 1!A1:G'
            ).execute()
            rows = result.get("values", [])
            df = pd.DataFrame(rows[1:], columns=rows[0])

            # Parse timestamps
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            df['Week'] = df['Timestamp'].dt.to_period("W").apply(lambda r: r.start_time)

            # Count attendance per section per week
            attendance_summary = df.groupby(['Week', 'Section']).size().unstack(fill_value=0)

            # Plot
            plt.figure(figsize=(12, 6))
            for section in attendance_summary.columns:
                plt.plot(attendance_summary.index, attendance_summary[section], marker='o', label=section)

            plt.xlabel("Week")
            plt.ylabel("Attendance Count")
            plt.title("Weekly Attendance by Section")
            plt.xticks(rotation=45)
            plt.legend(loc='upper left', bbox_to_anchor=(1, 1))
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)

            response = client.files_upload_v2(
                channel=command["channel_id"],
                file=buf,
                filename="attendance_by_section.png",
                title="Weekly Attendance by Section"
            )

            if response["ok"]:
                say("✅ Here is the attendance plot by section!")
            else:
                say("⚠️ Failed to upload the attendance plot.")

        except Exception as e:
            print(f"❌ Error in /plot_attendance: {e}")
            say(f"❌ Error generating attendance plot: {e}")

    @app.command("/plot_extensions")
    @log_command("/plot_extensions")
    def plot_extensions(ack, say, command, client):
        creds = Credentials.from_service_account_file("gspread-api.json", scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)

        result = service.spreadsheets().values().get(
            spreadsheetId=EXTENSIONS_ID, range=EXTENSIONS_RANGE
        ).execute()

        rows = result.get("values", [])
        if not rows:
            say("⚠️ No extension data found.")
            return

        df = pd.DataFrame(rows[1:], columns=rows[0])
        df.columns = df.columns.str.strip()

        # Parse and clean timestamps
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
        df = df.dropna(subset=["Timestamp"])

        # Count by day
        daily_counts = df["Timestamp"].dt.date.value_counts().sort_index()

        # Plot
        plt.figure(figsize=(10, 5))
        plt.plot(daily_counts.index, daily_counts.values, marker="o", linestyle="-")
        plt.xticks(rotation=45)
        plt.title("Daily Extension Requests")
        plt.xlabel("Date")
        plt.ylabel("Number of Requests")
        plt.grid(True)

        # Save to buffer
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)

        # Upload to Slack
        response = client.files_upload_v2(
            channel=command["channel_id"],
            file=buf,
            filename="extensions_plot.png",
            title="Daily Extension Requests"
        )

        if not response["ok"]:
            say("⚠️ Failed to upload plot.")

    @app.command("/get_grade")
    def get_grade(ack, say, command):
        ack()
        query = command["text"].strip()

        if not query:
            say("⚠️ Please provide a full name or SID. Example: `/get_grade Edwin Vargas Navarro` or `/get_grade 123456789`")
            return

        try:
            say(f"🔍 Fetching grade for `{query}` from Canvas...")
            course_config = _get_course_config_for_command(command)
            canvas_creds = course_config["canvas"]
            canvas = CanvasClient(
                token=canvas_creds["CANVAS_TOKEN"],
                base_url=canvas_creds["CANVAS_API_URL"]
            )
            course_id = canvas_creds["CANVAS_ID"]
            grade_data = canvas.get_student_grade(course_id, query)

            if grade_data is None:
                say(f"❌ No student found for `{query}`.")
            elif grade_data["score"] is None:
                say(f"ℹ️ Found *{grade_data['name']}* (SIS ID: `{grade_data['sis_id']}`), but no grade is available yet.")
            else:
                say(f"📊 *{grade_data['name']}* (SIS ID: `{grade_data['sis_id']}`) currently has a grade of *{grade_data['score']:.2f}%*.")

        except Exception as e:
            print(f"❌ Error in /get_grade: {e}")
            say(f"❌ Error fetching grade: {e}")

    @app.command("/plot_student_radar")
    def plot_student_radar(ack, say, command, client):
        ack()
        name = command["text"].strip()

        try:
            # Fetch Gradescope performance data
            course_config = _get_course_config_for_command(command)
            course_id = course_config.get("gradescope_id") or COURSE_ID
            GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=course_id)
            GC.update_performance_data()
            df = GC.get_student_performance_df()

            # Load lab attendance data from Google Sheets
            creds = Credentials.from_service_account_file("gspread-api.json", scopes=SCOPES)
            service = build('sheets', 'v4', credentials=creds)
            result = service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range='Form Responses 1!A1:G'
            ).execute()
            rows = result.get("values", [])
            lab_df = pd.DataFrame(rows[1:], columns=rows[0]) if rows else pd.DataFrame()

            # Fetch Ed involvement data
            edSlack = EdSlackAPI(command['team_domain'])
            raw_threads = edSlack.filtered_threads(edSlack.session, "all")
            raw_df = edSlack.process_json(raw_threads, edSlack.fields)  # raw with 'user'
            ed_posts_df = edSlack.compute_ed_posts_from_threads(raw_df)  # post counts
            # Ed posts df must include First Name, Last Name, Ed Posts

            # Generate radar plot
            plot_buf = generate_student_radar_plot(name, df, lab_df=lab_df, ed_df=ed_posts_df)

            # Upload to Slack
            client.files_upload_v2(
                channel=command["channel_id"],
                file=plot_buf,
                filename="radar_plot.png",
                title=f"Radar Plot for REDACTED"
            )
            say(f"✅ Here's the radar plot for REDACTED!")

        except Exception as e:
            print(f"❌ Error generating radar plot: {e}")
            say(f"❌ Error generating radar plot: {e}")


def generate_student_radar_plot(student_name, df, lab_df=None, ed_df=None):
    import matplotlib.pyplot as plt
    import numpy as np
    from scipy.stats import percentileofscore
    import io

    categories = ["Labs", "Homework", "Projects", "Exams", "Lab Attendance", "Ed Involvement"]
    scores = []

    # Standardize name matching
    def normalize_name(name):
        return name.strip().lower()

    student_name_norm = normalize_name(student_name)

    # Add normalized full name to df if not present
    if "Full Name" not in df.columns:
        df["Full Name"] = (df["First Name"].str.strip() + " " + df["Last Name"].str.strip()).str.lower()

    student_row = df[df["Full Name"] == student_name_norm]
    if student_row.empty:
        raise ValueError(f"Student '{student_name}' not found in performance data.")
    student = student_row.iloc[0]

    # Normalized sum scoring helper
    def normalized_sum(prefix):
        cols = [col for col in df.columns if col.startswith(prefix) and "Max Points" not in col]
        max_cols = [col + " - Max Points" for col in cols if col + " - Max Points" in df.columns]
        total = pd.to_numeric(student[cols], errors='coerce').sum()
        max_total = pd.to_numeric(student[max_cols], errors='coerce').sum()
        return round(min(5, total / max_total * 5), 2) if max_total > 0 else 0

    # Score 1: Labs
    scores.append(normalized_sum("Lab"))

    # Score 2: Homework
    scores.append(normalized_sum("Homework"))

    # Score 3: Projects
    scores.append(normalized_sum("Project"))

    # Score 4: Exams (average of Midterm and Final)
    def normalized_exam_score():
        exam_scores = []
        for exam in ["Midterm", "Final"]:
            exam_cols = [col for col in df.columns if col.startswith(exam) and "Max Points" not in col]
            max_cols = [col + " - Max Points" for col in exam_cols if col + " - Max Points" in df.columns]
            score = pd.to_numeric(student[exam_cols], errors='coerce').sum()
            max_score = pd.to_numeric(student[max_cols], errors='coerce').sum()
            if max_score > 0:
                exam_scores.append(score / max_score)
        return round(sum(exam_scores) / len(exam_scores) * 5, 2) if exam_scores else 0
    scores.append(normalized_exam_score())

    # Score 5: Lab Attendance
    lab_score = 0
    if lab_df is not None:
        if "Full Name" not in lab_df.columns:
            lab_df["Full Name"] = (lab_df["First Name"].str.strip() + " " + lab_df["Last Name"].str.strip()).str.lower()
        lab_matches = lab_df[lab_df["Full Name"] == student_name_norm]
        lab_score = min(5, lab_matches.shape[0])
    scores.append(lab_score)

    # Score 6: Ed Involvement (percentile)
    ed_score = 0
    if ed_df is not None and "Ed Posts" in ed_df.columns:
        if "Full Name" not in ed_df.columns:
            ed_df["Full Name"] = (ed_df["First Name"].str.strip() + " " + ed_df["Last Name"].str.strip()).str.lower()
        ed_df["Ed Posts"] = pd.to_numeric(ed_df["Ed Posts"], errors="coerce").fillna(0)
        student_ed = ed_df[ed_df["Full Name"] == student_name_norm]
        if not student_ed.empty:
            posts = student_ed.iloc[0]["Ed Posts"]
            ed_score = round(percentileofscore(ed_df["Ed Posts"], posts) / 100 * 5, 2)
    scores.append(ed_score)

    # Close radar loop
    scores += scores[:1]
    angles = [n / float(len(categories)) * 2 * np.pi for n in range(len(categories))]
    angles += angles[:1]

    # Plot
    plt.figure(figsize=(6, 6))
    ax = plt.subplot(111, polar=True)
    ax.plot(angles, scores, linewidth=2)
    ax.fill(angles, scores, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories)
    plt.title(f"Student Radar Plot: REDACTED", size=15, y=1.08)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close()

    return buf

def check_unanswered_edposts():
    try:
        now = datetime.now(pytz.timezone("America/Los_Angeles"))

        # Only run Monday–Friday
        if now.weekday() >= 5:
            print(f"📆 Skipping check at {now.strftime('%A %H:%M')} - weekend.")
            return

        # Only run during active hours
        if not (NOTIFY_HOURS[0] <= now.hour <= NOTIFY_HOURS[1]):
            print(f"⏰ Skipping check at {now.strftime('%H:%M')} - outside active hours.")
            return

        # Iterate over all configured courses (or all Slack workspaces if no course keys)
        course_keys = get_all_course_keys() or list(slack_app_by_team.keys())
        for team_domain in course_keys:
            try:
                course_config = get_course_config(team_domain)
                ed_course_id = course_config["edstem"]["ED_COURSE_ID"]
            except Exception as cfg_err:
                print(f"⚠️ Skipping team_domain '{team_domain}' due to config error: {cfg_err}")
                continue

            edSlack = EdSlackAPI(team_domain)
            overdue_posts = _build_unresolved_posts(edSlack, ed_course_id, now)

            if not overdue_posts:
                print(f"✅ All posts resolved for {team_domain} - no alerts to send.")
                continue

            slack_app = slack_app_by_team.get(team_domain)
            if not slack_app:
                print(f"⚠️ No Slack app for team_domain '{team_domain}' - skipping Ed post alert.")
                continue

            print(f"📣 Alerting for {len(overdue_posts)} Ed post(s) in {team_domain}.")

            header_message = (
                f":rotating_light: *Unanswered Ed Posts That Need Attention for `{team_domain}`!*\n"
                "React with :white_check_mark: when resolving a thread."
            )
            sections = _render_unresolved_sections(overdue_posts)
            _post_threaded_sections(slack_app.client, ALERT_CHANNEL, header_message, sections)

    except Exception as e:
        print(f"❌ Error checking Ed posts: {e}")

def main():
    global slack_app_by_team

    for team_domain, creds in SLACK_WORKSPACES.items():
        bot_token = creds.get("SLACK_BOT_TOKEN")
        app_token = creds.get("SLACK_APP_TOKEN")
        if not bot_token or not app_token:
            print(f"⚠️ Skipping workspace '{team_domain}': missing SLACK_BOT_TOKEN or SLACK_APP_TOKEN.")
            continue
        app = App(token=bot_token, name=f"seamless-{team_domain}")
        register_handlers(app)
        slack_app_by_team[team_domain] = app
        handler = SocketModeHandler(app, app_token)
        thread = threading.Thread(target=handler.start, daemon=True)
        thread.start()
        print(f"Started Slack handler for workspace: {team_domain}")

    scheduler = BackgroundScheduler(timezone="America/Los_Angeles")
    scheduler.add_job(
        check_unanswered_edposts,
        trigger='cron',
        hour='9,13,17',
        minute=0
    )
    scheduler.start()

    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
