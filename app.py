from edslack import EdSlackAPI

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

app = App(token=SLACK_BOT_TOKEN, name="seamless-bot")

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

@app.command("/list-commands")
def list_commands(ack, respond, command):
    ack()
    response = f"Hi {command['user_name']}! Here are the commands you can use:\n" + "/current_unresolved\n/top_questions\n"
    respond(response)

def main():
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()

if __name__ == "__main__":
    main()