from ed_utils import *

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import yaml
import os

with open("config/credentials.yml", 'r') as stream:
    try:
        credentials = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

SLACK_BOT_TOKEN=credentials['credentials']['SLACK_BOT_TOKEN']
SLACK_APP_TOKEN=credentials['credentials']['SLACK_APP_TOKEN']
ED_API = credentials['credentials']['ED_API']

app = App(token=SLACK_BOT_TOKEN, name="test-bot")

@app.event("app_mention")
def event_test(say):
    say("Hi there!")

@app.command("/current_unresolved")
def unresolved_info(ack, say, command):
    ack()
    session = get_session()
    unresolved_threads = filtered_threads(session, "unresolved")
    processed_unresolved = add_subthreads(process_user(process_json(unresolved_threads, fields)))
    say("Unresolved threads: " + str(len(unresolved_threads)) + "\n" + str(list(processed_unresolved["title"])))

@app.command("/top_questions")
def top_questions(ack, say, command):
    ack()
    date = command["text"]
    month, day, year = date.split("/")
    up_to = datetime(int(year), int(month), int(day))
    upto_threads = process_user(get_timeframe(up_to))
    upto_questions = upto_threads[upto_threads["type"] == "question"]
    say("Top categories: " + str(upto_questions["category"].value_counts()[:5]))

def main():
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()

if __name__ == "__main__":
    main()