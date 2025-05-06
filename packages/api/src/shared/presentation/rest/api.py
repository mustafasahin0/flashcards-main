import json
import ast
import re
import os
import time
from functools import wraps

# from django.contrib import admin
from django.urls import path
from groq import Groq
from ninja import NinjaAPI

from category.presentation.rest.api import router as category_router
from category.presentation.rest.containers import category_command, category_query
from flashcard.presentation.rest.api import router as flashcard_router
from flashcard.presentation.rest.containers import flashcard_command
from shared.domain.exception import ModelExistsError

api = NinjaAPI(
    title="Flashcards API",
    description="A demo API for Lazar's flashcards app",
)

api.add_router("categories", category_router)
api.add_router("flashcards", flashcard_router)


@api.get("/wipe-database")
def wipe_database(request):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "TRUNCATE TABLE flashcard_flashcard, category_category CASCADE;"
        )
    return {"message": "Database wiped successfully"}


def retry_on_error(max_attempts=3, delay_seconds=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except (json.JSONDecodeError, ValueError) as e:
                    attempts += 1
                    if attempts == max_attempts:
                        raise e
                    time.sleep(delay_seconds)
            return func(*args, **kwargs)

        return wrapper

    return decorator


@api.get("/populate-database")
def populate_database(request):
    try:
        # Fetch existing categories created by the user
        categories = category_query.get_all_categories()

        for category_obj in categories:
            try:
                # Generate flashcards for the existing category
                flashcards = generate_dummy_data(category_obj.name)
                for flashcard in flashcards:
                    try:
                        flashcard_command.create_flashcard(
                            question=flashcard["question"],
                            answer=flashcard["answer"],
                            category=category_obj,
                        )
                    except ModelExistsError as e:
                        print(f"Error creating flashcard {flashcard['question']}: {str(e)}")
            except Exception as e:
                print(f"Error generating flashcards for category {category_obj.name}: {str(e)}")

        print("Database populated successfully!!!!!")
    except Exception as e:
        raise ValueError(f"Unexpected error: {str(e)}")
    return {"message": "Database populated successfully"}


@retry_on_error(max_attempts=5)
def generate_dummy_data(category_name: str):
    # Generate dummy flashcards for the given category name via Groq API
    groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

    messages = [
        {
            "role": "system",
            "content": """You are a JSON generator. Only output valid, complete JSON arrays without any additional text or formatting. Escape any internal double quotes inside strings using \\" and ensure the output is strictly valid JSON with no extra formatting.""",
        },
        {
            "role": "user",
            "content": f"Generate a whimsical set of flashcards for the category '{category_name}'. For each flashcard, provide a \"question\" and an \"answer\". Escape any internal double quotes in the text with \\\". The questions and answers should be unique. Generate between 30 and 50 flashcards. Only output a valid JSON array (no line breaks) of objects with \"question\" and \"answer\" fields.",
        },
    ]

    try:
        response = groq.chat.completions.create(
            model="llama3-8b-8192",
            messages=messages,
        )

        raw_content = response.choices[0].message.content
        print(f"[populate-database] Raw Groq response for '{category_name}':", raw_content)
        json_string = raw_content
        # Clean the string
        json_string = json_string.strip()
        print(f"[populate-database] After strip and before replace for '{category_name}': {json_string}")
        json_string = json_string.replace("'", '"')
        json_string = json_string.replace("\n", "")
        json_string = json_string.replace("\t", "")
        json_string = json_string.replace("\\n", "")
        
        print(f"[populate-database] After basic replacements for '{category_name}': {json_string}")
        # Extract JSON array substring in case AI adds extra text
        start_idx = json_string.find("[")
        end_idx = json_string.rfind("]") + 1
        if start_idx != -1 and end_idx != -1:
            json_string = json_string[start_idx:end_idx]

        # Auto-fix common missing comma issues
        # Insert comma between 'question' and 'answer' fields if missing
        json_string = re.sub(r'("question"\s*:\s*".*?\")\s*("answer"\s*:\s*)', r'\1, \2', json_string)
        # Insert comma between JSON objects if missing
        json_string = re.sub(r'\}\s*\{', r'}, {', json_string)
        # Remove any trailing commas before closing arrays/objects
        json_string = re.sub(r',\s*\]', ']', json_string)
        json_string = re.sub(r',\s*\}', '}', json_string)

        # Validate JSON array structure
        if not (json_string.startswith("[") and json_string.endswith("]")):
            raise ValueError("Invalid JSON array structure")

        # Try parsing as JSON first; handle both dict and string elements; fallback if parsing yields no flashcards
        flashcards = []
        try:
            data = json.loads(json_string)
            if not isinstance(data, list):
                raise ValueError("Root element must be an array")
            for item in data:
                # Item as object
                if isinstance(item, dict) and "question" in item and "answer" in item:
                    flashcards.append({"question": item["question"], "answer": item["answer"]})
                # Item as stringified JSON
                elif isinstance(item, str):
                    try:
                        obj = json.loads(item)
                        if isinstance(obj, dict) and "question" in obj and "answer" in obj:
                            flashcards.append({"question": obj["question"], "answer": obj["answer"]})
                    except json.JSONDecodeError:
                        continue
            # If no flashcards found, trigger fallback
            if not flashcards:
                raise ValueError("No valid flashcards in JSON array, using fallback regex")
        except Exception:
            # Fallback: extract question/answer pairs directly
            questions = re.findall(r'"question"\s*:\s*"([^\"]*)"', json_string)
            answers = re.findall(r'"answer"\s*:\s*"([^\"]*)"', json_string)
            flashcards = [{"question": q, "answer": a} for q, a in zip(questions, answers)]

        return flashcards
    except Exception as e:
        raise ValueError(f"Unexpected error: {str(e)}")


urlpatterns = [
    # path("admin/", admin.site.urls),
    path("", api.urls),
]
