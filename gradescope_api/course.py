from __future__ import annotations


import json
import re
from io import StringIO
import pandas as pd
from typing import TYPE_CHECKING, Dict, List, Optional
from course_constants import *

from bs4 import BeautifulSoup
import io
from gradescope_api.errors import check_response
from gradescope_api.student import GradescopeStudent
from gradescope_api.assignment import GradescopeAssignment
from gradescope_api.errors import GradescopeAPIError, check_response
from gradescope_api.utils import get_url_id
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from scipy.stats import percentileofscore
if TYPE_CHECKING:
    from gradescope_api.client import GradescopeClient


from datetime import datetime, timedelta

import pytz
from dateutil.parser import parse
import matplotlib.pyplot as plt
import numpy as np

cached_performance_df = None

GRADESCOPE_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
import pandas as pd
import psycopg2
from io import StringIO

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# PostgreSQL connection parameters (loaded from .env)
dbname = os.getenv("DB_NAME")
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("DB_HOST")


conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host)

query = "SELECT * FROM c88c_gradescope;"

cur = conn.cursor()
cur.execute(query)
rows = cur.fetchall()
colnames = [desc[0] for desc in cur.description]
cached_performance_df = pd.DataFrame(rows, columns=colnames)
cur.close()
conn.close()


cached_performance_df = cached_performance_df.drop(cached_performance_df.columns[0], axis=1)


# GRADESCOPE_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# cached_performance_df = None
last_update_time = None


class GradescopeCourse:

    def __init__(self, _client: GradescopeClient, course_id: str) -> None:
        self._client = _client
        self.course_id = course_id
        self.assignments: dict = {}
        self.roster: List[GradescopeStudent] = []
        self.grades = None

    def get_url(self) -> str:
        return self._client.get_base_url() + f"/courses/{self.course_id}"

    def get_roster(self) -> List[GradescopeStudent]:
        if self.roster:
            return self.roster

        url = self._client.get_base_url() + f"/courses/{self.course_id}/memberships"
        response = self._client.session.get(url=url, timeout=20)
        check_response(response, "failed to get roster")

        soup = BeautifulSoup(response.content, "html.parser")
        for row in soup.find_all("tr", class_="rosterRow"):
            nameButton = row.find("button", class_="js-rosterName")
            role = row.find("option", selected=True).text
            if nameButton and role == "Student":
                user_id = nameButton["data-url"].split("?user_id=")[1]
                editButton = row.find("button", class_="rosterCell--editIcon")
                if editButton:
                    data_email = editButton["data-email"]
                    data_cm: Dict = json.loads(editButton["data-cm"])
                    self.roster.append(
                        GradescopeStudent(
                            _client=self._client,
                            user_id=user_id,
                            full_name=data_cm.get("full_name"),
                            first_name=data_cm.get("first_name"),
                            last_name=data_cm.get("last_name"),
                            sid=data_cm.get("sid"),
                            email=data_email,
                        )
                    )

        return self.roster

    def get_student(self, sid: Optional[str] = None, email: Optional[str] = None) -> Optional[GradescopeStudent]:
        assert sid or email
        roster = self.get_roster()
        for student in roster:
            if sid != None and student.sid == sid:
                return student
            if email != None and student.email == email:
                return student
        return None

    def get_assignment(
        self, assignment_id: Optional[str] = None, assignment_url: Optional[str] = None
    ) -> Optional[GradescopeAssignment]:
        assert assignment_id or assignment_url
        assignment_id = assignment_id or get_url_id(url=assignment_url, kind="assignments")
        return GradescopeAssignment(_client=self._client, _course=self, assignment_id=assignment_id)

    def get_grades(self, assignment_re: Optional[dict] = {r'(.*)': lambda s: s.sum(axis = 1)}):
        if self.grades is None:
            url = f"{self._client.get_base_url()}/courses/{self.course_id}/gradebook.csv"
            grades = pd.read_csv(StringIO(self._client.session.get(url, timeout=20).content.decode('utf8')))
            max_points = grades.columns[grades.columns.isin(grades.columns + ' - Max Points')]
            assignments = max_points.str.replace(' - Max Points', '')
            self.grades = (grades, max_points, assignments)
        grades, max_points, assignments = self.grades
        grades = grades.copy()
        groupers = {}
        for assignment, max_pt in zip(assignments, max_points):
            for possible_assignment_re, agg_fn in assignment_re.items():
                match = re.match(possible_assignment_re, assignment)
                if match:
                    name = match.group(1)
                    if (name, agg_fn) not in groupers:
                        groupers[(name, agg_fn)] = [[], [], []]
                    groupers[(name, agg_fn)][0].append(assignment)
                    groupers[(name, agg_fn)][1].append(max_pt)
                    groupers[(name, agg_fn)][2].append(re.sub(' Written$', '', assignment) + ' - Lateness (H:M:S)')

        for (name, agg_fn), (assignment, max_pt, lateness) in groupers.items():
            n1, n2 = f'REGEX {name}', f'REGEX {name} - Max Points'
            assert n1 not in grades.columns and n2 not in grades.columns
            grades[n1] = agg_fn(grades[assignment] * grades[lateness].apply(lambda s: s.str.contains('^00:')).to_numpy())
            grades[n2] = agg_fn(grades[max_pt])
            col = ['Email', n1, n2]
            # TODO
            if name == 'Final' or name == 'Midterm':
                grades[col].to_csv(f'../clobber/{name}.csv')
            yield (name, grades, col)

    def get_assignments(self):
        if self.assignments:
            print(f"✅ Cached assignments: {self.assignments.keys()}")  # Debugging log
            return self.assignments

        url = self._client.get_base_url() + f"/courses/{self.course_id}/assignments"
        response = self._client.session.get(url=url, timeout=20)
        
        print(f"🔍 Gradescope Response Status: {response.status_code}")  # Debugging
        print(f"🔍 Gradescope Response Text: {response.text[:500]}")  # Print first 500 chars for debugging

        check_response(response, "failed to get assignments")

        soup = BeautifulSoup(response.content, "html.parser")
        for row in soup.find_all("button"):
            html = str(row)
            soup = BeautifulSoup(html, 'html.parser')
            button = soup.find('button')

            if button:
                assignment_id = button.get('data-assignment-id')
                assignment_name = button.get_text().strip()
                self.assignments[assignment_name] = assignment_id

        print(f"📌 Final Available Assignments: {list(self.assignments.keys())}")  # Debugging
        return self.assignments


    def get_extensions(self, assignment_name: str):
        assignment_list = self.get_assignments()
        assignment_id = assignment_list[assignment_name]

        ext = f"/courses/{self.course_id}/assignments/{assignment_id}/extensions"
        url = self._client.get_base_url() + ext
        response = self._client.session.get(url=url, timeout=20)
        check_response(response, "failed to get extensions")
        soup = BeautifulSoup(response.content, "html.parser")
        table = soup.find('table')
        data = {'Name': [], 'Due Date': [], 'Late Due Date': []}
        # extreact information and make it to a DataFrame
        if table:
            rows = table.find_all('tr')
            current_row = None
            for row in rows:
                columns = row.find_all('td')
                if columns:
                    i = 0
                    name = None
                    due_date = None
                    late_due_date = None
                    for column in columns:
                        text = column.get_text(strip=True)
                        if i == 0:
                            name = text
                        elif i == 3:
                            due_date = text
                        elif i == 4:
                            late_due_date = text
                        i += 1
                    if name and due_date and late_due_date:
                        data['Name'].append(name)
                        data['Due Date'].append(due_date)
                        data['Late Due Date'].append(late_due_date)
        
        df = pd.DataFrame(data)
        
        time_elements = soup.find_all('time')

        # Create a dictionary to store the results
        date_times = {}

        # Iterate through the time elements and extract the data
        for time_element in time_elements:
            label = time_element.find_previous('div', class_='type-subheading').text.strip()
            datetime_value = time_element['datetime']
            date_times[label] = datetime_value

        # Print the results
        second_datetime_value = list(date_times.values())[1]
        df["Actual Due Date"] = second_datetime_value
        print(df)
        return df

    def apply_extension_course(self, email: str, time_limit_multiplier = 1):
        """
        A new method to apply an extension to a Gradescope course, given an email and time limit multiplier.
        """

        # First, fetch the extensions page for the assignment, which contains a student roster as well as
        # the due date (and hard due date) for the assignment.
        course_id = self.course_id
        response = self._client.session.get(
            f"https://www.gradescope.com/courses/{course_id}/extensions", timeout=20
        )

        if not response.ok:
            return

        # Once we fetch the page, parse out the data (students + due dates)
        soup = BeautifulSoup(response.content, "html.parser")
        

        if soup.find(
            "div", {"data-react-class": "AddExtension"}) is None:
            # If a 'NoneType' error occurred, delete the existing extension first
            self.delete_course_extension(email)
        props = soup.find(
            "li", {"data-react-class": "AddExtension"})["data-react-props"]
        data = json.loads(props)
        students = {row["email"]: row["id"]
                    for row in data.get("students", [])}
        user_id = students.get(email)
        if not user_id:
            raise GradescopeAPIError("student email not found")

        # Make the post request to create the extension
        url = self.get_url() + "/extensions"
        headers = {
            "Host": "www.gradescope.com",
            "Origin": "https://www.gradescope.com",
            "Referer": url,
            "X-CSRF-Token": self._client._get_token(url, meta="csrf-token"),
        }
        payload = {
            "override": {
                "user_id": user_id,
                "settings": {
                    "time_limit": {
                        "type": "extension_multiplier",
                        "value": time_limit_multiplier
                    }
                }
            }
        }

        # Make the POST request to update the extension
        response = self._client.session.post(url, headers=headers, json=payload, timeout=20)
        check_response(response, "Updating the extension failed")

    def delete_course_extension(self, email: str):
    # Fetch the extensions page to find the deletePath for the extension
        course_id = self.course_id
        response = self._client.session.get(
            f"https://www.gradescope.com/courses/{course_id}/extensions", timeout=20
        )

        if not response.ok:
            return

        # Parse the page using BeautifulSoup
        soup = BeautifulSoup(response.content, "html.parser")

        # Find the "DeleteExtension" element
        delete_extension_element = soup.find("div", {"data-react-class": "DeleteExtension"})

        if delete_extension_element:
            # Extract the data-react-props attribute
            data_props = delete_extension_element.get("data-react-props")

            if data_props:
                # Parse the data-react-props JSON
                props = json.loads(data_props)

                # Extract the "path" attribute from the props
                if "path" in props:
                    delete_path = props["path"]
                    # Find the user_id for the given email
                    add_extension_element = soup.find("li", {"data-react-class": "AddExtension"})
                    data = json.loads(add_extension_element["data-react-props"])
                    students = {row["email"]: row["id"] for row in data.get("students", [])}
                    user_id = students.get(email)

                    if user_id:
                        # Make the POST request to delete the extension
                        delete_url = f"https://www.gradescope.com{delete_path}"
                        headers = {
                            "Host": "www.gradescope.com",
                            "Origin": "https://www.gradescope.com",
                            "Referer": delete_url,
                            "X-CSRF-Token": self._client._get_token(delete_url, meta="csrf-token"),
                        }

                        payload = {
                            "override": {
                                "user_id": user_id
                            }
                        }

                        # Make the POST request to delete the extension
                        response = self._client.session.post(delete_url, headers=headers, json=payload, timeout=20)

                        if response.ok:
                            return 


    def get_student_id(self, name: str = None, email_input: str = None, SID: str = None):
        """
        Retrieves the user ID based on name, email, or SID. If none of these match a student, it returns an error message.
        """
        course_id = self.course_id
        response = self._client.session.get(
            f"https://www.gradescope.com/courses/{course_id}/memberships", timeout=20
        )

        if not response.ok:
            return "Error: Failed to fetch course membership data from Gradescope."

        soup = BeautifulSoup(response.content, "html.parser")

        # Extract student data from the HTML
        students_data = []
        for row in soup.find_all("button", class_="rosterCell--editIcon"):
            data_cm = json.loads(row["data-cm"]) if "data-cm" in row.attrs else {}
            full_name = data_cm.get("full_name", "")
            sid = data_cm.get("sid", "")
            email = row.get("data-email", "")
            students_data.append({"full_name": full_name, "SID": sid, "email": email})

        df = pd.DataFrame(students_data)

        # Get user_id mapping
        response2 = self._client.session.get(
            f"https://www.gradescope.com/courses/{course_id}/extensions", timeout=20
        )
        
        if not response2.ok:
            return "Error: Failed to fetch extension data from Gradescope."

        soup2 = BeautifulSoup(response2.content, "html.parser")
        props2 = soup2.find("li", {"data-react-class": "AddExtension"})

        if props2 is None:
            return "Error: Unable to extract user ID mappings from Extensions page."

        data = json.loads(props2["data-react-props"])
        students = {row["email"]: row["id"] for row in data.get("students", [])}
        df['user_id'] = df['email'].map(students)

        df.dropna(inplace=True)
        df['user_id'] = df['user_id'].astype(str).str[:-2]

        # Debugging: Print DataFrame
        print(f"📌 Full Student Data: \n{df.head()}")

        if name:
            filtered_df = df[df['full_name'].str.lower() == name.lower()]
            if filtered_df.empty:
                return f"Error: No user found for name '{name}'"
            user_id = filtered_df['user_id'].values[0]
            print(f"✅ Found user ID for {name}: {user_id}")
            return user_id

        if SID:
            filtered_df = df[df['SID'] == SID]
            if filtered_df.empty:
                return f"Error: No user found for SID '{SID}'"
            user_id = filtered_df['user_id'].values[0]
            print(f"✅ Found user ID for SID {SID}: {user_id}")
            return user_id

        if email_input:
            filtered_df = df[df['email'].str.lower() == email_input.lower()]
            if filtered_df.empty:
                return f"Error: No user found for email '{email_input}'"
            user_id = filtered_df['user_id'].values[0]
            print(f"✅ Found user ID for {email_input}: {user_id}")
            return user_id

        print(df)  # If no filters are given, print the whole DataFrame
        return df
    
    def download_grades_csv(self):
        url = f"https://www.gradescope.com/courses/{self.course_id}/gradebook.csv"
        response = self._client.session.get(url)
        csv = pd.read_csv(io.StringIO(response.text))
        return csv
    
    def update_performance_data(self):
        global cached_performance_df, last_update_time

        new_performance_df = self.download_grades_csv()

        # Update the cache only if the data has changed
        cached_performance_df = new_performance_df
        last_update_time = datetime.now()
    
    def get_student_performance(self, name: str):
        if cached_performance_df is None:
            return "No data loaded. Run `/refresh_gradescope` first."

        df = cached_performance_df.copy()
        df["Full Name"] = df["First Name"] + " " + df["Last Name"]
        student_row = df[df["Full Name"] == name]

        if student_row.empty:
            return f"No student found with name '{name}'."

        student_row = student_row.iloc[0]

        # 1. Compute total score
        score_cols = [col for col in df.columns if not col.endswith("Max Points") and not col.endswith("Submission Time") and not col.endswith("Lateness (H:M:S)") and col not in ["First Name", "Last Name", "SID", "Email", "Sections", "Full Name"]]
        max_cols = [col + " - Max Points" for col in score_cols if col + " - Max Points" in df.columns]

        student_total = pd.to_numeric(student_row[score_cols], errors='coerce').sum()
        max_total = pd.to_numeric(student_row[max_cols], errors='coerce').sum()

        if max_total == 0:
            return "Max score is 0 — grading not set up properly."

        percentage = student_total / max_total * 100

        # 2. Count late assignments
        late_cols = [col for col in df.columns if col.endswith("Lateness (H:M:S)")]
        num_late = 0
        for col in late_cols:
            try:
                lateness = student_row[col]
                if pd.notna(lateness) and isinstance(lateness, str):
                    hours = int(lateness.split(":")[0])
                    if hours > 0:
                        num_late += 1
            except Exception:
                continue

        # 3. Count submissions
        submitted = pd.to_numeric(student_row[score_cols], errors='coerce').notna().sum()
        total_assignments = len(score_cols)

        status = "Above" if percentage >= GRADE_THRESHOLD else "Below"

        return (
            f"Average: {percentage:.2f} ({status} Threshold)\n"
            f"Assignments Submitted: {submitted}/{total_assignments}\n"
            f"Late Assignments: {num_late}\n"
            f"Last Updated: {last_update_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def get_student_performance_df(self):
        return cached_performance_df.copy()
