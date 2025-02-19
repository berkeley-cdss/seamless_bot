import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Access variables
COURSE_ID = os.getenv("COURSE_ID")
GS_USERNAME = os.getenv("GS_USERNAME")
GS_PASSWORD = os.getenv("GS_PASSWORD")
GRADE_THRESHOLD = float(os.getenv("GRADE_THRESHOLD",0))