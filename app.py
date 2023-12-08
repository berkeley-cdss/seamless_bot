from edslack import EdSlackAPI
import sys
sys.path.append('..')

from course_constants import *

import getpass
from gradescope_api.client import GradescopeClient
from gradescope_api.course import GradescopeCourse

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from datetime import datetime
import yaml
import os




with open("config/credentials.yml", 'r') as stream:
    try:
        credentials = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

SLACK_BOT_TOKEN=credentials['credentials']['SLACK_BOT_TOKEN']
SLACK_APP_TOKEN=credentials['credentials']['SLACK_APP_TOKEN']

app = App(token=SLACK_BOT_TOKEN, name="test-bot")

@app.event("app_mention")
def event_test(say):
    say("Hi there!")

@app.command("/current_unresolved")
def unresolved_info(ack, say, command):
    ack()
    edSlack = EdSlackAPI(command['team_domain'])
    unresolved_threads = edSlack.filtered_threads(edSlack.session, "unresolved")
    processed_unresolved = edSlack.add_subthreads(edSlack.process_user(edSlack.process_json(unresolved_threads, edSlack.fields)))
    response = f"Hi {command['user_name']}! We have {str(len(unresolved_threads))} unresolved threads right now.\n" + str(list(processed_unresolved["title"]))
    say(response)

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
    assignment = command["text"]
    GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id = COURSE_ID)

    df = GC.get_extensions(assignment)
    df_str = df.to_string()
    response = f"Hi {command['user_name']}! Here are the extension information for {assignment}\n{df_str}"
    say(response)

@app.command("/get_user_id")
def get_user_id(ack, say, command):
    ack()
    input = command["text"]
    parsed_values = input.split(",")
    Name,email,SID = parsed_values
    
    GC = GradescopeClient(GS_USERNAME, GS_PASSWORD).get_course(course_id = COURSE_ID)
    if Name == 'None' and email == 'None' and SID == 'None':
        df = GC.get_student_id(None,None,None)
        response = f"Hi {command['user_name']}! Here are the gradescope user id info for course{COURSE_ID}\n{df}"
    elif Name !='None':
        id = GC.get_student_id(Name,None,None)
        response = f"Hi {command['user_name']}! Here is the gradescope user id for{Name}\n{id}"
    elif email!='None':
        id = GC.get_student_id(None,email,None)
        response = f"Hi {command['user_name']}! Here is the gradescope user id for{email}\n{id}"
    elif SID != 'None':
        id = GC.get_student_id(None,None,SID)
        response = f"Hi {command['user_name']}! Here is the gradescope user id for{SID}\n{id}"

 
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

def main():
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()

if __name__ == "__main__":
    main()
