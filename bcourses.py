import requests

class CanvasClient:
    def __init__(self, token, base_url):
        self.headers = {"Authorization": f"Bearer {token}"}
        self.base_url = base_url

    def get_course_id(self, course_name):
        response = requests.get(f"{self.base_url}/courses", headers=self.headers)
        for course in response.json():
            if course["name"].lower() == course_name.lower():
                return course["id"]
        return None
    
    def get_student_grade(self, course_id, query):
        enrollments_url = f"{self.base_url}/courses/{course_id}/enrollments?per_page=100"
        query = query.strip().lower()
        all_enrollments = []

        while enrollments_url:
            response = requests.get(enrollments_url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            all_enrollments.extend(data)

            # Pagination logic
            links = response.links
            if "next" in links:
                enrollments_url = links["next"]["url"]
            else:
                enrollments_url = None

        # Search student by full name or SIS ID
        for enrollment in all_enrollments:
            if enrollment.get("type") != "StudentEnrollment":
                continue

            user = enrollment.get("user", {})
            full_name = user.get("name", "").strip().lower()
            sis_id = str(user.get("sis_user_id", "")).strip().lower()

            if query == full_name or query == sis_id:
                score = enrollment.get("grades", {}).get("current_score")
                return {
                    "name": user.get("name", "N/A"),
                    "sis_id": sis_id,
                    "score": score
                }

        return None
