from edslack import EdSlackAPI
import sys
sys.path.append('..')

from course_constants import *

import getpass
from gradescope_api.client import GradescopeClient
from gradescope_api.course import GradescopeCourse

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from datetime import datetime
import yaml
import os
import io
import base64




with open("config/credentials.yml", 'r') as stream:
    try:
        credentials = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

SLACK_BOT_TOKEN=credentials['credentials']['SLACK_BOT_TOKEN']
SLACK_APP_TOKEN=credentials['credentials']['SLACK_APP_TOKEN']

app = App(token=SLACK_BOT_TOKEN, name="test-bot")


@app.event("app_mention")
def event_test(event, say):
    print(f"Received event: {event}")  # Debugging log
    say("Hi there!")


@app.command("/current_unresolved")
def unresolved_info(ack, say, command):
    ack()
    print(f"🔍 Received command: {command}")  # Log incoming command
    
    try:
        edSlack = EdSlackAPI(command['team_domain'])
        print("✅ Initialized EdSlackAPI")  # Log API initialization
        
        unresolved_threads = edSlack.filtered_threads(edSlack.session, "unresolved")
        print(f"🔍 Found {len(unresolved_threads)} unresolved threads")  # Log thread count
        
        if not unresolved_threads:
            say("No unresolved threads found.")
            return
        
        processed_unresolved = edSlack.add_subthreads(
            edSlack.process_user(edSlack.process_json(unresolved_threads, edSlack.fields))
        )
        print("✅ Processed unresolved threads")  # Log processing

        response = f"Hi {command['user_name']}! We have {str(len(unresolved_threads))} unresolved threads right now.\n"
        response += str(list(processed_unresolved["title"]))
        
        say(response)
    except Exception as e:
        print(f"❌ Error in /current_unresolved: {e}")
        say(f"An error occurred: {e}")


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
        GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=COURSE_ID)
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

    GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id=COURSE_ID)

    if Name.lower() == 'none' and email.lower() == 'none' and SID.lower() == 'none':
        df = GC.get_student_id(None, None, None)
        response = f"Hi {command['user_name']}! Here are the Gradescope user ID details for course {COURSE_ID}:\n{df}"
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

@app.command("/get_student_performance")
def get_student_performance(ack, say, command):
    ack()
    name = command["text"]
    GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id = COURSE_ID)

    result = GC.get_student_performance(name)
    response = f"Hi {command['user_name']}! Here are the approximate performance for {name}\n{result}"
    # print(response)
    say(response)

@app.command("/refresh_gradescope")
def get_student_performance(ack, say, command):
    ack()
    GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id = COURSE_ID)

    GC.update_performance_data()
    response = f"Hi {command['user_name']}! Gradescope Data Had Updated Successfully!"
    # print(response)
    say(response)

@app.command("/plot_questions")
def plot_questions(ack, say, command, client):
    ack()
    say(":bar_chart: Generating a plot of student questions throughout the semester...")

    try:
        edSlack = EdSlackAPI(command['team_domain'])

        # Get date range from Slack command (e.g., "01/01/2024")
        date = command["text"]
        month, day, year = date.split("/")
        up_to = datetime(int(year), int(month), int(day))

        # Fetch and filter student questions
        upto_threads = edSlack.process_user(edSlack.get_timeframe(up_to))
        upto_questions = upto_threads[upto_threads["type"] == "question"]

        # Check if we have data
        if upto_questions.empty:
            say("⚠️ No student questions found in this timeframe.")
            return

        # Ensure 'created_at' column exists
        if "created_at" not in upto_questions.columns:
            say(f"⚠️ Error: 'created_at' column not found! Available columns: {', '.join(upto_questions.columns)}")
            return

        # Convert 'created_at' to datetime using .loc[] to avoid warnings
        upto_questions.loc[:, "created_at"] = pd.to_datetime(upto_questions["created_at"])

        # Count number of questions per day
        question_counts = upto_questions.resample("D", on="created_at").size()

        # Generate the plot
        plt.figure(figsize=(10, 5))
        plt.plot(question_counts.index, question_counts.values, marker="o", linestyle="-", label="Student Questions")
        plt.xlabel("Date")
        plt.ylabel("Number of Questions")
        plt.title("Number of Student Questions Over Time")
        plt.xticks(rotation=45)
        plt.legend()
        plt.grid(True)

        # Save plot to a buffer
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)

        # Upload plot to Slack correctly using client.files_upload()
        response = client.files_upload_v2(
            channel=command["channel_id"],  # Send the file to the correct Slack channel
            file=buf,
            filename="questions_plot.png",
            title="Student Questions Over Time"
        )

        # Confirm success
        if response["ok"]:
            say("✅ Here is the plot of student questions over time.")
        else:
            say("⚠️ Failed to upload the plot.")

    except Exception as e:
        print(f"❌ Error in /plot_questions: {e}")
        say(f"⚠️ Error generating plot: {e}")

def main():
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()

if __name__ == "__main__":
    main()
