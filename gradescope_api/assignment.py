from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import pytz
from bs4 import BeautifulSoup
from dateutil.parser import parse

from gradescope_api.errors import GradescopeAPIError, check_response

if TYPE_CHECKING:
    from gradescope_api.client import GradescopeClient
    from gradescope_api.course import GradescopeCourse

GRADESCOPE_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class GradescopeAssignment:
    def __init__(self, _client: GradescopeClient, _course: GradescopeCourse, assignment_id: str) -> None:
        self._client = _client
        self._course = _course
        self.assignment_id = assignment_id

    def get_url(self) -> str:
        return self._course.get_url() + f"/assignments/{self.assignment_id}"

    def apply_extension(self, email: str, num_days = -1, due_date = None):
        """
        A new method to apply an extension to a Gradescope assignment, given an email and a number of days.
        """
        if num_days < 0 and due_date is None:
            raise ValueError("no extension specified")

        # First, fetch the extensions page for the assignment, which contains a student roster as well as
        # the due date (and hard due date) for the assignment.
        course_id = self._course.course_id
        assignment_id = self.assignment_id
        response = self._client.session.get(
            f"https://www.gradescope.com/courses/{course_id}/assignments/{assignment_id}/extensions", timeout=20
        )
        # check_response(response, "could not load assignment")
        if not response.ok:
            return

        # Once we fetch the page, parse out the data (students + due dates)
        soup = BeautifulSoup(response.content, "html.parser")
        props = soup.find(
            "li", {"data-react-class": "AddExtension"})["data-react-props"]
        data = json.loads(props)
        students = {row["email"]: row["id"]
                    for row in data.get("students", [])}
        user_id = students.get(email)
        if not user_id:
            raise GradescopeAPIError("student email not found")

        # A helper method to transform the date
        def transform_date(datestr: str):
            dt = pytz.timezone("US/Pacific").localize(parse(datestr))
            if num_days < 0:
                dt = due_date
            else:
                dt = dt + timedelta(num_days)
            return dt.astimezone(pytz.utc)

        assignment = data["assignment"]
        new_due_date = transform_date(assignment["due_date"])

        if assignment["hard_due_date"]:
            new_hard_due_date = transform_date(assignment["hard_due_date"])

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
                    "due_date": {"type": "absolute", "value": new_due_date.strftime(GRADESCOPE_DATETIME_FORMAT)}
                },
            }
        }

        if assignment["hard_due_date"]:
            payload["override"]["settings"]["hard_due_date"] = {
                "type": "absolute",
                        "value": new_hard_due_date.strftime(GRADESCOPE_DATETIME_FORMAT),
            }

        response = self._client.session.post(
            url, headers=headers, json=payload, timeout=20)
        check_response(response, "creating an extension failed")

