import requests
from datetime import datetime
import pytz
from dateutil.parser import parse
import pandas as pd
from edapi import EdAPI
import numpy as np
import os
import yaml

with open("config/credentials.yml", 'r') as stream:
    try:
        credentials = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

BASE_URL = "https://us.edstem.org/api/"
os.environ['ED_API_TOKEN'] = credentials['credentials']['ED_API']
ed = EdAPI()
ed.login()
course_id = credentials['credentials']['ED_COURSE_ID']
fields = ['id', 'user_id', 'type', 'title', 'content', 'category', 'subcategory', 'subsubcategory',
                       'unresolved_count', 'is_answered', 'is_anonymous', 'is_megathread', 'created_at', 'user']

def process_json(json_response, fields):
  processed_threads = {}
  for field in fields:
    processed_threads[field] = []

  for thread in json_response:
    for field in fields:
      processed_threads[field].append(thread[field])
    processed_threads['json'] = json_response

  return pd.DataFrame(processed_threads)

def process_user(threads):
  threads["user id"] = threads["user"].apply(lambda x: x["id"])
  threads.drop(columns=["user"], inplace=True)
  return threads

def add_subthreads(threads):
  threads = threads.copy()
  comments, answers, jsons = [], [], []
  for i in threads.index:
    thread = threads.loc[i]
    thread_json = ed.get_thread(thread['id'])
    comments.append(thread_json["comments"])
    answers.append(thread_json["answers"])
    jsons.append(thread_json)
  threads["comments"] = comments
  threads["answers"] = answers
  threads["subthread_json"] = jsons
  return threads

def get_timeframe(up_to):
  reached = False
  offset = 0
  up_to = pytz.UTC.localize(up_to)
  all_threads = pd.DataFrame()

  while not reached:
    curr_threads = ed.list_threads(course_id=course_id, limit=25, offset=offset)
    curr_threads = add_subthreads(process_json(curr_threads, fields))
    student_threads = curr_threads[curr_threads['user'].apply(lambda x: x['course_role'] == 'student')]
    is_after = student_threads['created_at'].apply(lambda x: parse(x) >= up_to)
    if all_threads.shape[0] == 0:
      all_threads = curr_threads
    else:
      all_threads = pd.concat((all_threads, curr_threads))

    if len(is_after) == 0 or not is_after.iloc[-1]:
      reached = True
    offset += 100
  all_threads = all_threads[all_threads['created_at'].apply(lambda x: parse(x) >= up_to)]
  return all_threads.reset_index(drop=True)

def get_session():
  session = requests.Session()
  session.headers.update({"Authorization": f"Bearer {os.environ['ED_API_TOKEN']}"})
  return session

def filtered_threads(session, filter):
  response = session.get(BASE_URL + "courses/" + str(course_id) + "/threads", params={"filter": filter})

  if response.ok:
    return response.json()["threads"]